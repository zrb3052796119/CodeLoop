from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.memory_hybrid_qwen_v2_evaluator as evaluator
import scripts.memory_hybrid_resolver_pressure_evaluator as pressure_evaluator
from minicode.evaluation_one_shot import EvaluationOneShotReceipt
from minicode.memory_hybrid import (
    HYBRID_PROMOTION_INFERENCE_PROFILE,
    assess_hybrid_activation,
    build_hybrid_decision_contract,
    evidence_fingerprint,
)


def _entry(entry_id: str) -> dict[str, object]:
    return {
        "id": entry_id,
        "memory_text": f"synthetic memory {entry_id}",
        "metadata": {},
        "domains": [],
        "stored_files": [],
    }


def _dataset() -> dict[str, object]:
    backgrounds = [_entry(f"background-{index:03d}") for index in range(80)]
    cases = []
    for index in range(84):
        positive = index < 24
        cases.append(
            {
                "id": f"case-{index:03d}",
                "query": f"synthetic query {index}",
                "current_files": [],
                "active_domains": [],
                "entry": _entry(f"target-{index:03d}"),
                "expected_relevant": positive,
                "polarity": "positive" if positive else "hard_negative",
                "relation": f"category-{index % 12:02d}",
            }
        )
    return {"background_entries": backgrounds, "cases": cases}


def _write_holdout(root: Path, dataset: dict[str, object]) -> tuple[Path, str, str]:
    path = root / "holdout.json"
    raw = json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = root / "MANIFEST.sha256"
    manifest.write_text(f"{digest}  holdout.json\n", encoding="utf-8")
    return path, digest, hashlib.sha256(manifest.read_bytes()).hexdigest()


def _identity() -> dict[str, object]:
    return {
        "provider": "qwen",
        "model_id": "text-embedding-v3",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        "dimension": 1024,
        "representation_version": "memory-structured-v1",
        "canary_version": "embedding-canary-v1",
        "canary_fingerprint": "a" * 64,
    }


def _one_shot_claim(
    *,
    dataset_id: str,
    holdout_sha256: str,
    output_artifact: str,
    marker_name: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "holdout_sha256": holdout_sha256,
        "output_artifact": output_artifact,
        "marker_path": marker_name,
        "claimed_at_utc": "2026-09-03T00:00:00Z",
    }


def _pressure_report() -> dict[str, object]:
    cases = [
        {
            "case_id": pressure_evaluator._report_identifier(
                "case", f"pressure-case-{index:03d}"
            ),
            "entry_id": pressure_evaluator._report_identifier(
                "entry", f"pressure-entry-{index:03d}"
            ),
            "relation_sha256": hashlib.sha256(
                f"pressure-relation-{index:03d}".encode()
            ).hexdigest(),
            "contested_reason": "unrelated",
            "primary_reason": "semantic_equivalence",
            "decision": "irrelevant",
            "confidence": 0.99,
            "support_kind": "none",
            "disqualifier": "unrelated",
            "query_span": {"start": 0, "end": 8},
            "memory_span": {"start": 0, "end": 8},
            "query_segment_kind": "query.task",
            "memory_segment_kind": "memory.content",
            "approved": False,
            "protocol_failure": False,
        }
        for index in range(60)
    ]
    checks = {
        "one_shot_claim_bound": True,
        "sealed_case_count": True,
        "raw_relevant_count_zero": True,
        "approved_count_zero": True,
        "protocol_failure_batches_zero": True,
        "protocol_failure_cases_zero": True,
        "approved_upper_bound_at_most_0_05": True,
        "exact_batch_call_count": True,
        "content_free_report": True,
    }
    report = {
        "schema_version": pressure_evaluator.REPORT_SCHEMA_VERSION,
        "protocol_version": evaluator.HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "one_shot_claim": _one_shot_claim(
            dataset_id=pressure_evaluator.DATASET_ID,
            holdout_sha256=pressure_evaluator.EXPECTED_HOLDOUT_SHA256,
            output_artifact=evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
            marker_name=pressure_evaluator.ONE_SHOT_MARKER_NAME,
        ),
        "production_authority": False,
        "holdout": {
            "dataset_id": pressure_evaluator.DATASET_ID,
            "holdout_sha256": pressure_evaluator.EXPECTED_HOLDOUT_SHA256,
            "manifest_sha256": pressure_evaluator.EXPECTED_MANIFEST_SHA256,
            "case_count": 60,
        },
        "implementation_freeze": pressure_evaluator.implementation_freeze(),
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "verifier_route": evaluator.qwen_verifier_route_identity(),
        **build_hybrid_decision_contract("qwen3.7-plus"),
        "resolver_pressure": {
            "counts": {
                "case_count": 60,
                "raw_relevant_count": 0,
                "approved_count": 0,
                "protocol_failure_batches": 0,
                "protocol_failure_cases": 0,
            },
            "metrics": {
                "raw_relevant_rate": 0.0,
                "approved_rate": 0.0,
                "approved_one_sided_exact_95_upper": (
                    evaluator.one_sided_exact_binomial_upper(0, 60)
                ),
            },
            "usage": {
                "model_call_count": 3,
                "input_tokens": 1,
                "output_tokens": 1,
            },
            "cases": cases,
        },
        "acceptance_gate": {"passed": True, "checks": checks},
    }
    report["report_fingerprint"] = evidence_fingerprint(report)
    return report


@pytest.fixture(autouse=True)
def _allow_synthetic_pressure_release(monkeypatch: pytest.MonkeyPatch) -> None:
    fingerprint = str(_pressure_report()["report_fingerprint"])
    monkeypatch.setattr(
        evaluator,
        "HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        fingerprint,
    )
    monkeypatch.setattr(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        fingerprint,
    )


def _pressure_receipt() -> dict[str, object]:
    return evaluator.build_resolver_pressure_receipt(
        _pressure_report(),
        artifact=evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    )


def _complete_projected_hybrid_diagnostics(
    *, candidate_count: int, preliminary_accepted_count: int
) -> dict[str, object]:
    verifier_calls = math.ceil(candidate_count / evaluator.DEFAULT_BATCH_SIZE)
    challenger_calls = (
        math.ceil(preliminary_accepted_count / evaluator.DEFAULT_BATCH_SIZE)
        if preliminary_accepted_count
        else 0
    )
    resolver_calls = 0
    model_call_count = verifier_calls + challenger_calls + resolver_calls
    return evaluator._project_hybrid_diagnostics(
        {
            "provider": "qwen_strict_llm_v3",
            "provider_cache_reused": False,
            "cache_hit": False,
            "dense_candidate_count": candidate_count,
            "dense_lane_count": candidate_count,
            "lexical_rescue_count": 0,
            "candidate_count": candidate_count,
            "adjudicated_count": candidate_count,
            "deterministic_accepted_count": 0,
            "deterministic_acceptance_reasons": {},
            "deterministic_rejected_count": 0,
            "deterministic_rejection_reasons": {},
            "query_gate_rejected_count": 0,
            "preliminary_accepted_count": preliminary_accepted_count,
            "challenged_count": preliminary_accepted_count,
            "challenger_accepted_count": preliminary_accepted_count,
            "resolver_attempted_count": 0,
            "resolver_input_rejected_count": 0,
            "resolver_disagreement_count": 0,
            "resolver_accepted_count": 0,
            "semantic_accepted_count": preliminary_accepted_count,
            "accepted_count": preliminary_accepted_count,
            "exact_gate_rejected_count": 0,
            "candidate_budget_rejected_count": 0,
            "model_call_count": model_call_count,
            "input_tokens": model_call_count * 10,
            "output_tokens": model_call_count * 5,
            "verifier_model_call_count": verifier_calls,
            "verifier_input_tokens": verifier_calls * 10,
            "verifier_output_tokens": verifier_calls * 5,
            "challenger_model_call_count": challenger_calls,
            "challenger_input_tokens": challenger_calls * 10,
            "challenger_output_tokens": challenger_calls * 5,
            "resolver_model_call_count": resolver_calls,
            "resolver_input_tokens": 0,
            "resolver_output_tokens": 0,
        }
    )


def _passing_report() -> dict[str, object]:
    dataset = _dataset()
    frozen = evaluator.FrozenHoldout(
        dataset=dataset,
        file_sha256=evaluator.EXPECTED_HOLDOUT_SHA256,
        manifest_sha256=evaluator.EXPECTED_MANIFEST_SHA256,
    )
    dense_case_rows = []
    canonical_case_rows = []
    categories: dict[str, dict[str, object]] = {}
    for case in dataset["cases"]:  # type: ignore[index]
        positive = case["polarity"] == "positive"
        relation_hash = evaluator._relation_digest(case["relation"])
        target_id = evaluator._report_identifier("entry", case["entry"]["id"])
        case_id = evaluator._report_identifier("case", case["id"])
        dense_case_rows.append(
            {
                "case_id": case_id,
                "target_id": target_id,
                "polarity": case["polarity"],
                "relation_sha256": relation_hash,
                "target_rank": 1 if positive else 21,
                "target_in_top_20": positive,
                "top_20_ids_sha256": "c" * 64,
            }
        )
        background_ids = [
            evaluator._report_identifier("entry", entry["id"])
            for entry in dataset["background_entries"][:20]  # type: ignore[index]
        ]
        candidate_ids = (
            [target_id, *background_ids[:19]] if positive else background_ids
        )
        accepted_ids = [target_id] if positive else []
        canonical_case_rows.append(
            {
                "case_id": case_id,
                "target_id": target_id,
                "polarity": case["polarity"],
                "relation_sha256": relation_hash,
                "candidate_ids": candidate_ids,
                "admitted_ids": accepted_ids,
                "rendered_ids": accepted_ids,
                "target_candidate": positive,
                "target_admitted": positive,
                "target_rendered": positive,
                "target_reason_codes": (
                    ["hybrid_semantic_admission"] if positive else []
                ),
                "foreign_ids": [],
                "lifecycle_leakage_ids": [],
                "safety_leakage_ids": [],
                "forbidden_leakage_ids": [],
                "provider_fallback": False,
                "hybrid": _complete_projected_hybrid_diagnostics(
                    candidate_count=len(candidate_ids),
                    preliminary_accepted_count=int(positive),
                ),
            }
        )
        if positive:
            category = categories.setdefault(
                relation_hash, {"hits": 0, "total": 0, "recall_at_20": 1.0}
            )
            category["hits"] += 1
            category["total"] += 1
    dense = {
        "eligible_corpus_size": 164,
        "positive_hits": 24,
        "positive_total": 24,
        "candidate_recall_at_20": 1.0,
        "category_macro_recall_at_20": 1.0,
        "categories": categories,
        "cases": dense_case_rows,
    }
    canonical = {
        "counts": {
            "positive_total": 24,
            "hard_negative_total": 60,
            "candidate_positive_hits": 24,
            "admitted_positive_hits": 24,
            "rendered_positive_hits": 24,
            "false_positive_admissions": 0,
            "false_positive_renders": 0,
            "hard_negative_target_admissions": 0,
            "hard_negative_target_renders": 0,
            "lifecycle_leakage_count": 0,
            "safety_leakage_count": 0,
            "forbidden_leakage_count": 0,
            "forbidden_indexed_count": 0,
            "provider_fallback_count": 0,
            "id_mismatch_count": 0,
        },
        "metrics": {
            "canonical_candidate_positive_recall": 1.0,
            "admitted_positive_recall": 1.0,
            "admitted_precision": 1.0,
            "rendered_positive_recall": 1.0,
            "rendered_precision": 1.0,
            "hard_negative_target_admission_rate": 0.0,
            "hard_negative_admission_one_sided_exact_95_upper": (
                evaluator.one_sided_exact_binomial_upper(0, 60)
            ),
            "hard_negative_target_render_rate": 0.0,
            "hard_negative_one_sided_exact_95_upper": (
                evaluator.one_sided_exact_binomial_upper(0, 60)
            ),
        },
        "forbidden_indexed_ids": [],
        "cases": canonical_case_rows,
    }
    usage = {
        "embedding": {
            "completed_call_count": 263,
            "failed_call_count": 0,
            "input_tokens": 263,
            "output_tokens": 0,
            "usage_unavailable_count": 0,
        },
        "embedding_stages": {
            stage: {
                "completed_call_count": count,
                "failed_call_count": 0,
                "input_tokens": count,
                "output_tokens": 0,
                "usage_unavailable_count": 0,
            }
            for stage, count in {
                "identity": 2,
                "global_documents": 9,
                "global_query": 84,
                "canonical_documents": 84,
                "canonical_query": 84,
                "unattributed": 0,
            }.items()
        },
        "verifier": {
            "completed_call_count": 84,
            "failed_call_count": 0,
            "input_tokens": 840,
            "output_tokens": 420,
            "usage_unavailable_count": 0,
        },
        "challenger": {
            "completed_call_count": 24,
            "failed_call_count": 0,
            "input_tokens": 240,
            "output_tokens": 120,
            "usage_unavailable_count": 0,
        },
        "resolver": {
            "completed_call_count": 0,
            "failed_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "usage_unavailable_count": 0,
        },
        "other": {
            "completed_call_count": 0,
            "failed_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "usage_unavailable_count": 0,
        },
    }
    return evaluator.build_report(
        frozen=frozen,
        implementation_freeze=evaluator.build_implementation_freeze(),
        runtime_implementation_freeze=evaluator.build_hybrid_runtime_freeze(
            evaluator.ROOT
        ),
        resolver_pressure_receipt=_pressure_receipt(),
        one_shot_claim=_one_shot_claim(
            dataset_id=evaluator.HOLDOUT_VERSION,
            holdout_sha256=evaluator.EXPECTED_HOLDOUT_SHA256,
            output_artifact=evaluator.REPORT_ARTIFACT,
            marker_name=evaluator.ONE_SHOT_MARKER_NAME,
        ),
        identity=_identity(),
        verifier_model="qwen3.7-plus",
        global_dense=dense,
        canonical=canonical,
        usage=usage,
    )


def _synchronize_model_usage(report: dict[str, object]) -> None:
    """Keep a deliberately edited report's content-free usage join honest."""

    rows = report["canonical"]["cases"]  # type: ignore[index]
    for lane in ("verifier", "challenger", "resolver"):
        usage = report["usage"][lane]  # type: ignore[index]
        usage["completed_call_count"] = sum(
            row["hybrid"][f"{lane}_model_call_count"] for row in rows
        )
        usage["input_tokens"] = sum(
            row["hybrid"][f"{lane}_input_tokens"] for row in rows
        )
        usage["output_tokens"] = sum(
            row["hybrid"][f"{lane}_output_tokens"] for row in rows
        )


def test_preflight_accepts_only_the_exact_hash_counts_and_schema(
    tmp_path: Path,
) -> None:
    path, digest, manifest_digest = _write_holdout(tmp_path, _dataset())

    frozen = evaluator.load_frozen_holdout(
        path,
        expected_sha256=digest,
        expected_manifest_sha256=manifest_digest,
    )

    assert frozen.summary["background_count"] == 80
    assert frozen.summary["case_count"] == 84
    assert frozen.summary["positive_count"] == 24
    assert frozen.summary["hard_negative_count"] == 60
    assert frozen.file_sha256 == digest


def test_preflight_requires_an_exact_byte_pinned_manifest_binding(
    tmp_path: Path,
) -> None:
    path, digest, _manifest_digest = _write_holdout(tmp_path, _dataset())
    manifest = tmp_path / "MANIFEST.sha256"
    manifest.write_text(
        f"{digest}  holdout.json extra-field\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not bind holdout.json"):
        evaluator.load_frozen_holdout(
            path,
            expected_sha256=digest,
            expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )


def test_implementation_freeze_binds_evaluator_and_behavior_critical_sources() -> None:
    freeze = evaluator.build_implementation_freeze()

    assert freeze["schema_version"] == "hybrid-evaluator-freeze-v1"
    assert freeze["file_count"] == len(evaluator.BEHAVIOR_CRITICAL_FILES)
    assert [row["path"] for row in freeze["files"]] == list(
        evaluator.BEHAVIOR_CRITICAL_FILES
    )
    assert all(len(row["sha256"]) == 64 for row in freeze["files"])
    assert len(freeze["aggregate_sha256"]) == 64


def test_runtime_freeze_contains_the_audited_semantic_dependency_closure() -> None:
    required = {
        "minicode/advisory_lock.py",
        "minicode/cost_tracker.py",
        "minicode/domain_classifier.py",
        "minicode/env_file.py",
        "minicode/memory_audit_contract.py",
        "minicode/memory_authority_publisher.py",
        "minicode/memory_blob_store.py",
        "minicode/memory_hybrid_promotion_contract.py",
        "minicode/memory_path_identity.py",
        "minicode/memory_provenance.py",
        "minicode/memory_write_contract.py",
        "minicode/reflection_evidence.py",
        "minicode/reflection_synthesis.py",
    }

    assert required <= set(evaluator.HYBRID_RUNTIME_BEHAVIOR_FILES)
    assert "minicode/memory_hybrid_release.py" not in set(
        evaluator.HYBRID_RUNTIME_BEHAVIOR_FILES
    )


def test_holdout_hash_failure_occurs_in_content_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _digest, _manifest_digest = _write_holdout(tmp_path, _dataset())
    monkeypatch.setattr(evaluator, "EXPECTED_HOLDOUT_SHA256", "f" * 64)

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        evaluator.load_frozen_holdout(path)


def test_pressure_report_is_validated_before_the_independent_holdout(
    tmp_path: Path,
) -> None:
    pressure = _pressure_report()
    pressure["production_authority"] = True
    pressure["report_fingerprint"] = evidence_fingerprint(pressure)
    pressure_path = tmp_path / "pressure-report.json"
    pressure_path.write_text(json.dumps(pressure), encoding="utf-8")

    with pytest.raises(ValueError, match="pressure report authority mismatch"):
        evaluator.load_resolver_pressure_report(
            pressure_path,
            artifact_reference=(evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT),
        )


def test_authoritative_runner_exposes_no_identity_or_factory_overrides() -> None:
    assert evaluator.DEFAULT_HOLDOUT.parent.name == evaluator.HOLDOUT_VERSION
    assert set(inspect.signature(evaluator.run_evaluation).parameters) == {"env_file"}

    with pytest.raises(TypeError):
        evaluator.run_evaluation(  # type: ignore[call-arg]
            env_file=Path(".env"),
            one_shot_marker_path=Path("alternate-marker.json"),
        )


def test_authoritative_runner_validates_holdout_before_claiming_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(evaluator, "build_implementation_freeze", lambda: {})
    monkeypatch.setattr(evaluator, "build_hybrid_runtime_freeze", lambda _root: {})
    monkeypatch.setattr(
        evaluator,
        "load_resolver_pressure_report",
        lambda *_args, **_kwargs: {},
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
        evaluator.run_evaluation()

    assert order == ["holdout_preflight"]


def _post_claim_embedding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> evaluator.PromotionEvaluationRunFailed:
    frozen = evaluator.FrozenHoldout(
        dataset=_dataset(),
        file_sha256=evaluator.EXPECTED_HOLDOUT_SHA256,
        manifest_sha256=evaluator.EXPECTED_MANIFEST_SHA256,
    )
    claim = _one_shot_claim(
        dataset_id=evaluator.HOLDOUT_VERSION,
        holdout_sha256=evaluator.EXPECTED_HOLDOUT_SHA256,
        output_artifact=evaluator.REPORT_ARTIFACT,
        marker_name=evaluator.ONE_SHOT_MARKER_NAME,
    )

    class FailingEncoder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @property
        def identity(self) -> dict[str, object]:
            raise RuntimeError("provider-secret-that-must-never-be-published")

    monkeypatch.setattr(
        evaluator,
        "load_resolver_pressure_report",
        lambda *_args, **_kwargs: _pressure_receipt(),
    )
    monkeypatch.setattr(evaluator, "load_frozen_holdout", lambda _path: frozen)
    monkeypatch.setattr(evaluator, "create_exact_subagent_verifier", lambda *_a, **_k: object())
    monkeypatch.setattr(
        evaluator,
        "create_openai_compatible_embedding_client",
        lambda _root: object(),
    )
    monkeypatch.setattr(evaluator, "OpenAICompatibleEmbeddingEncoder", FailingEncoder)
    monkeypatch.setattr(
        evaluator,
        "claim_evaluation_run",
        lambda *_args, **_kwargs: EvaluationOneShotReceipt(**claim),
    )
    monkeypatch.setattr("minicode.env_file.apply_env_file", lambda _paths: None)
    monkeypatch.setattr("minicode.config.load_runtime_config", lambda _root: {})

    with pytest.raises(evaluator.PromotionEvaluationRunFailed) as captured:
        evaluator.run_evaluation()
    return captured.value


def test_post_claim_provider_failure_is_content_free_and_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = _post_claim_embedding_failure(monkeypatch)
    report = failure.failure_report
    serialized = json.dumps(report, sort_keys=True)

    assert report["failure_stage"] == "embedding_identity"
    assert report["failure_kind"] == "provider_or_transport_error"
    assert report["production_authority"] is False
    assert "provider-secret-that-must-never-be-published" not in serialized
    assert "provider-secret-that-must-never-be-published" not in str(failure)
    assert evaluator.validate_post_claim_failure_report(report) == report
    with pytest.raises(ValueError, match="promotion report schema mismatch"):
        evaluator.validate_content_free_promotion_report(report)


def test_main_persists_post_claim_failure_without_production_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = _post_claim_embedding_failure(monkeypatch)
    report_path = tmp_path / evaluator.REPORT_ARTIFACT
    evidence_path = tmp_path / evaluator.PRODUCTION_EVIDENCE_ARTIFACT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(evaluator, "ROOT", tmp_path)
    monkeypatch.setattr(
        evaluator,
        "run_evaluation",
        lambda **_kwargs: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(sys, "argv", ["evaluator", "--acknowledge-one-shot"])

    with pytest.raises(SystemExit) as exited:
        evaluator.main()

    assert exited.value.code == 2
    assert json.loads(report_path.read_text(encoding="utf-8")) == failure.failure_report
    assert not evidence_path.exists()


def test_main_does_not_publish_a_preclaim_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / evaluator.REPORT_ARTIFACT
    evidence_path = tmp_path / evaluator.PRODUCTION_EVIDENCE_ARTIFACT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(evaluator, "ROOT", tmp_path)
    monkeypatch.setattr(
        evaluator,
        "run_evaluation",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("local preflight")),
    )
    monkeypatch.setattr(sys, "argv", ["evaluator", "--acknowledge-one-shot"])

    with pytest.raises(ValueError, match="local preflight"):
        evaluator.main()

    assert not report_path.exists()
    assert not evidence_path.exists()


def test_pressure_report_validation_is_full_and_content_free() -> None:
    report = _pressure_report()

    validated = evaluator.validate_resolver_pressure_report(report)
    receipt = evaluator.build_resolver_pressure_receipt(
        validated,
        artifact=evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    )

    assert receipt == {
        "artifact": evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        "report_fingerprint": report["report_fingerprint"],
        "holdout_sha256": pressure_evaluator.EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": pressure_evaluator.EXPECTED_MANIFEST_SHA256,
        "counts": {
            "case_count": 60,
            "raw_relevant_count": 0,
            "approved_count": 0,
        },
        "approved_one_sided_exact_95_upper": pytest.approx(0.0487029133),
    }


def test_authoritative_pressure_loader_requires_release_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _pressure_report()
    path = tmp_path / "pressure-report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(
        evaluator,
        "HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        "",
    )

    with pytest.raises(ValueError, match="not release-allowlisted"):
        evaluator.load_resolver_pressure_report(
            path,
            artifact_reference=evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        )

    monkeypatch.setattr(
        evaluator,
        "HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        report["report_fingerprint"],
    )
    receipt = evaluator.load_resolver_pressure_report(
        path,
        artifact_reference=evaluator.HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    )
    assert receipt["report_fingerprint"] == report["report_fingerprint"]

    tampered = json.loads(json.dumps(report))
    tampered["resolver_pressure"]["cases"][0]["query"] = "sealed text"
    tampered["report_fingerprint"] = evidence_fingerprint(tampered)
    with pytest.raises(ValueError, match="case row schema mismatch"):
        evaluator.validate_resolver_pressure_report(tampered)

    tampered = json.loads(json.dumps(report))
    tampered["implementation_freeze"]["fingerprint"] = "f" * 64
    tampered["report_fingerprint"] = evidence_fingerprint(tampered)
    with pytest.raises(ValueError, match="authority mismatch"):
        evaluator.validate_resolver_pressure_report(tampered)

    tampered = json.loads(json.dumps(report))
    tampered["resolver_pressure"]["counts"]["raw_relevant_count"] = 1
    tampered["report_fingerprint"] = evidence_fingerprint(tampered)
    with pytest.raises(ValueError, match="metrics are inconsistent"):
        evaluator.validate_resolver_pressure_report(tampered)

    for field in ("holdout_sha256", "manifest_sha256"):
        tampered = json.loads(json.dumps(report))
        tampered["holdout"][field] = "f" * 64
        tampered["report_fingerprint"] = evidence_fingerprint(tampered)
        with pytest.raises(ValueError, match="holdout receipt is invalid"):
            evaluator.validate_resolver_pressure_report(tampered)


def test_schema_rejects_label_polarity_disagreement() -> None:
    dataset = _dataset()
    dataset["cases"][0]["expected_relevant"] = False  # type: ignore[index]

    with pytest.raises(ValueError, match="label and polarity disagree"):
        evaluator.validate_holdout_schema(dataset)


def test_exact_subagent_route_is_required_for_verifier() -> None:
    calls: list[tuple[str, object, dict[str, object]]] = []

    def factory(model: str, tools: object, runtime: dict[str, object]) -> object:
        calls.append((model, tools, runtime))
        return SimpleNamespace(
            runtime={
                "model": model,
                "provider": "custom",
                "customBaseUrl": evaluator.QWEN_VERIFIER_BASE_URL,
            }
        )

    runtime = {
        "subagentRoutingEnabled": True,
        "subagentModels": {"plan": "qwen3.7-plus"},
        "subagentProvider": "openai-compatible",
        "subagentBaseUrl": evaluator.QWEN_VERIFIER_BASE_URL,
        "subagentApiKey": "synthetic-key",
    }

    adapter = evaluator.create_exact_subagent_verifier(
        runtime, "qwen3.7-plus", adapter_factory=factory
    )

    assert adapter.runtime["model"] == "qwen3.7-plus"
    assert calls[0][0:2] == ("qwen3.7-plus", None)
    assert calls[0][2]["temperature"] == 0
    assert calls[0][2]["maxOutputTokens"] == 6000
    assert calls[0][2]["modelMaxRetries"] == 1
    assert calls[0][2]["modelTimeoutSeconds"] == 90
    assert calls[0][2]["enableThinking"] is False

    with pytest.raises(RuntimeError, match="exact_configured"):
        evaluator.create_exact_subagent_verifier(
            runtime, "qwen3.6-flash", adapter_factory=factory
        )
    assert len(calls) == 1

    wrong_endpoint = {
        **runtime,
        "subagentBaseUrl": "https://example.invalid/v1",
    }
    with pytest.raises(RuntimeError, match="exact_configured"):
        evaluator.create_exact_subagent_verifier(
            wrong_endpoint, "qwen3.7-plus", adapter_factory=factory
        )
    assert len(calls) == 1


def test_evaluation_evidence_uses_the_v7_canonical_decision_contract() -> None:
    evidence = evaluator._evaluation_evidence(
        _identity(),
        "qwen3.7-plus",
        _pressure_receipt(),
        _one_shot_claim(
            dataset_id=evaluator.HOLDOUT_VERSION,
            holdout_sha256=evaluator.EXPECTED_HOLDOUT_SHA256,
            output_artifact=evaluator.REPORT_ARTIFACT,
            marker_name=evaluator.ONE_SHOT_MARKER_NAME,
        ),
    )
    decision_contract = build_hybrid_decision_contract("qwen3.7-plus")

    assert evidence["schema_version"] == "3.0"
    assert evidence["protocol_version"] == "hybrid-canonical-v8.1"
    assert evidence["inference_profile"] == dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
    assert evidence["verifier"] == decision_contract["verifier"]
    assert evidence["challenger"] == decision_contract["challenger"]
    assert evidence["resolver"] == decision_contract["resolver"]
    assert evidence["report_fingerprint"] == evidence_fingerprint(evidence)


def test_evaluation_bootstrap_satisfies_the_real_activation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _one_shot_claim(
        dataset_id=evaluator.HOLDOUT_VERSION,
        holdout_sha256=evaluator.EXPECTED_HOLDOUT_SHA256,
        output_artifact=evaluator.REPORT_ARTIFACT,
        marker_name=evaluator.ONE_SHOT_MARKER_NAME,
    )
    evidence = evaluator._evaluation_evidence(
        _identity(), "qwen3.7-plus", _pressure_receipt(), claim
    )
    evidence_path = tmp_path / "evaluation-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evidence["report_fingerprint"],
    )

    activation = assess_hybrid_activation(
        requested=True,
        evidence_path=evidence_path,
        model_path=None,
        embedding_provider="qwen",
        allow_remote_embedding=True,
        _evaluation_bootstrap=True,
    )

    assert activation.active is True
    assert activation.reason == "evaluation_bootstrap_activated"


def test_usage_collector_accounts_for_resolver_separately() -> None:
    collector = evaluator.UsageCollector()
    collector.emit(
        "model.completed",
        payload={
            "purpose": "memory_hybrid_resolver",
            "usage": {
                "source": "provider",
                "inputTokens": 19,
                "outputTokens": 7,
            },
        },
    )

    usage = collector.snapshot()

    assert set(usage) == {
        "embedding",
        "embedding_stages",
        "verifier",
        "challenger",
        "resolver",
        "other",
    }
    assert usage["resolver"] == {
        "completed_call_count": 1,
        "failed_call_count": 0,
        "input_tokens": 19,
        "output_tokens": 7,
        "usage_unavailable_count": 0,
    }
    assert usage["other"]["completed_call_count"] == 0
    assert usage["embedding_stages"]["unattributed"]["completed_call_count"] == 0


def test_projected_hybrid_diagnostics_include_resolver_counts() -> None:
    diagnostics = evaluator._project_hybrid_diagnostics(
        {
            "provider": "qwen_strict_llm_v3",
            "resolver_disagreement_count": 3,
            "resolver_accepted_count": 2,
        }
    )

    assert set(diagnostics) == evaluator._HYBRID_DIAGNOSTIC_KEYS
    assert diagnostics["resolver_disagreement_count"] == 3
    assert diagnostics["resolver_accepted_count"] == 2


def test_canonical_hybrid_diagnostics_unwrap_the_real_provider_envelope() -> None:
    diagnostics = evaluator._project_canonical_hybrid_diagnostics(
        {
            "active": True,
            "fallback": False,
            "candidate_count": 7,
            "accepted_count": 1,
            "provider": {
                "provider": "qwen_strict_llm_v3",
                "dense_candidate_count": 20,
                "candidate_count": 7,
                "resolver_disagreement_count": 3,
                "resolver_accepted_count": 1,
            },
        }
    )

    assert set(diagnostics) == evaluator._HYBRID_DIAGNOSTIC_KEYS
    assert diagnostics["provider"] == "qwen_strict_llm_v3"
    assert diagnostics["dense_candidate_count"] == 20
    assert diagnostics["candidate_count"] == 7
    assert diagnostics["resolver_disagreement_count"] == 3
    assert diagnostics["resolver_accepted_count"] == 1


def test_canonical_hybrid_diagnostics_keep_fallback_content_free() -> None:
    diagnostics = evaluator._project_canonical_hybrid_diagnostics(
        {
            "active": False,
            "fallback": True,
            "failure_reason": "provider secret must not reach the report",
            "provider": {},
        }
    )

    assert set(diagnostics) == evaluator._HYBRID_DIAGNOSTIC_KEYS
    assert diagnostics["provider"] == ""
    assert diagnostics["model_call_count"] == 0
    assert "secret" not in json.dumps(diagnostics)


def test_missing_provider_diagnostics_are_counted_as_fail_closed_fallback() -> None:
    fallback, diagnostics = evaluator._canonical_hybrid_report_fields(
        {"active": True, "fallback": False, "provider": {}}
    )

    assert fallback is True
    assert diagnostics["provider"] == ""


def test_partial_expected_provider_diagnostics_are_fail_closed() -> None:
    fallback, diagnostics = evaluator._canonical_hybrid_report_fields(
        {
            "active": True,
            "fallback": False,
            "candidate_count": 0,
            "provider": {
                "provider": "qwen_strict_llm_v3",
                "cache_hit": False,
            },
        },
        observed_candidate_count=0,
    )

    assert fallback is True
    assert diagnostics["provider"] == "qwen_strict_llm_v3"


def test_complete_provider_diagnostics_are_authoritative() -> None:
    provider = {
        "provider": "qwen_strict_llm_v3",
        "provider_cache_reused": False,
        "cache_hit": False,
        "dense_candidate_count": 1,
        "dense_lane_count": 1,
        "lexical_rescue_count": 0,
        "candidate_count": 1,
        "adjudicated_count": 1,
        "deterministic_accepted_count": 0,
        "deterministic_acceptance_reasons": {},
        "deterministic_rejected_count": 0,
        "deterministic_rejection_reasons": {},
        "query_gate_rejected_count": 0,
        "preliminary_accepted_count": 0,
        "challenged_count": 0,
        "challenger_accepted_count": 0,
        "resolver_attempted_count": 0,
        "resolver_input_rejected_count": 0,
        "resolver_disagreement_count": 0,
        "resolver_accepted_count": 0,
        "semantic_accepted_count": 0,
        "accepted_count": 0,
        "exact_gate_rejected_count": 0,
        "candidate_budget_rejected_count": 0,
        "model_call_count": 1,
        "input_tokens": 10,
        "output_tokens": 5,
        "verifier_model_call_count": 1,
        "verifier_input_tokens": 10,
        "verifier_output_tokens": 5,
        "challenger_model_call_count": 0,
        "challenger_input_tokens": 0,
        "challenger_output_tokens": 0,
        "resolver_model_call_count": 0,
        "resolver_input_tokens": 0,
        "resolver_output_tokens": 0,
    }
    fallback, diagnostics = evaluator._canonical_hybrid_report_fields(
        {
            "active": True,
            "fallback": False,
            "candidate_count": 1,
            "accepted_count": 0,
            "provider": provider,
        },
        observed_candidate_count=1,
        observed_admitted_count=0,
    )

    assert fallback is False
    assert diagnostics["candidate_count"] == 1


def test_nonfallback_report_rejects_tampered_candidate_accounting() -> None:
    report = _passing_report()
    row = report["canonical"]["cases"][0]
    row["candidate_ids"].pop()
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="candidate count disagrees"):
        evaluator.validate_content_free_promotion_report(report)


def test_sealed_case_rows_cannot_claim_an_adjudication_cache_hit() -> None:
    report = _passing_report()
    row = report["canonical"]["cases"][0]
    row["hybrid"]["cache_hit"] = True
    for field in (
        "model_call_count",
        "input_tokens",
        "output_tokens",
        "verifier_model_call_count",
        "verifier_input_tokens",
        "verifier_output_tokens",
        "challenger_model_call_count",
        "challenger_input_tokens",
        "challenger_output_tokens",
        "resolver_model_call_count",
        "resolver_input_tokens",
        "resolver_output_tokens",
    ):
        row["hybrid"][field] = 0
    _synchronize_model_usage(report)
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="cannot reuse adjudication cache"):
        evaluator.validate_content_free_promotion_report(report)


def test_nonfallback_report_rejects_tampered_dense_lane_accounting() -> None:
    report = _passing_report()
    row = report["canonical"]["cases"][0]
    row["hybrid"]["dense_lane_count"] -= 1
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="stage accounting is inconsistent"):
        evaluator.validate_content_free_promotion_report(report)


def test_dense_protected_lane_cannot_be_relabelled_as_lexical_rescue() -> None:
    diagnostics = _complete_projected_hybrid_diagnostics(
        candidate_count=20,
        preliminary_accepted_count=1,
    )
    diagnostics["dense_lane_count"] = 19
    diagnostics["lexical_rescue_count"] = 1

    assert evaluator._hybrid_stage_counts_are_coherent(diagnostics) is False


def test_nonfallback_report_rejects_tampered_stage_accounting() -> None:
    report = _passing_report()
    row = report["canonical"]["cases"][0]
    row["hybrid"]["adjudicated_count"] -= 1
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="stage accounting is inconsistent"):
        evaluator.validate_content_free_promotion_report(report)


def test_nonfallback_report_rejects_tampered_challenge_accounting() -> None:
    report = _passing_report()
    row = next(
        item
        for item in report["canonical"]["cases"]
        if item["polarity"] == "hard_negative"
    )
    row["hybrid"]["challenged_count"] = 1
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="stage accounting is inconsistent"):
        evaluator.validate_content_free_promotion_report(report)


def test_nonfallback_report_rejects_tampered_resolver_accounting() -> None:
    report = _passing_report()
    row = next(
        item
        for item in report["canonical"]["cases"]
        if item["polarity"] == "hard_negative"
    )
    row["hybrid"]["preliminary_accepted_count"] = 1
    row["hybrid"]["challenged_count"] = 1
    row["hybrid"]["resolver_attempted_count"] = 1
    row["hybrid"]["model_call_count"] += 2
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="stage accounting is inconsistent"):
        evaluator.validate_content_free_promotion_report(report)


def test_resolver_acceptance_cannot_overlap_challenger_acceptance() -> None:
    diagnostics = _complete_projected_hybrid_diagnostics(
        candidate_count=20,
        preliminary_accepted_count=1,
    )
    diagnostics.update(
        {
            "resolver_attempted_count": 1,
            "resolver_input_rejected_count": 0,
            "resolver_disagreement_count": 1,
            "resolver_accepted_count": 1,
            "semantic_accepted_count": 2,
            "accepted_count": 2,
            "resolver_model_call_count": 1,
            "resolver_input_tokens": 10,
            "resolver_output_tokens": 5,
            "model_call_count": diagnostics["model_call_count"] + 1,
            "input_tokens": diagnostics["input_tokens"] + 10,
            "output_tokens": diagnostics["output_tokens"] + 5,
        }
    )

    assert evaluator._hybrid_stage_counts_are_coherent(diagnostics) is False


def test_resolver_input_rejection_accounting_is_coherent_without_model_call() -> None:
    diagnostics = _complete_projected_hybrid_diagnostics(
        candidate_count=20,
        preliminary_accepted_count=2,
    )
    diagnostics.update(
        {
            "challenger_accepted_count": 1,
            "resolver_attempted_count": 1,
            "resolver_input_rejected_count": 1,
            "resolver_disagreement_count": 1,
            "resolver_accepted_count": 0,
            "semantic_accepted_count": 1,
            "accepted_count": 1,
        }
    )

    assert evaluator._hybrid_stage_counts_are_coherent(diagnostics) is True

    diagnostics["resolver_input_rejected_count"] = 2
    assert evaluator._hybrid_stage_counts_are_coherent(diagnostics) is False


def test_nonfallback_report_rejects_tampered_admission_accounting() -> None:
    report = _passing_report()
    row = report["canonical"]["cases"][0]
    row["admitted_ids"] = []
    row["rendered_ids"] = []
    row["target_admitted"] = False
    row["target_rendered"] = False
    counts = report["canonical"]["counts"]
    counts["admitted_positive_hits"] -= 1
    counts["rendered_positive_hits"] -= 1
    metrics = report["canonical"]["metrics"]
    metrics["admitted_positive_recall"] = 23 / 24
    metrics["rendered_positive_recall"] = 23 / 24
    checks = evaluator.acceptance_checks(report)
    report["acceptance_gate"] = {
        "passed": all(checks.values()),
        "checks": checks,
    }
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="accepted count disagrees"):
        evaluator.validate_content_free_promotion_report(report)


def test_inactive_canonical_envelope_is_counted_as_fail_closed_fallback() -> None:
    fallback, diagnostics = evaluator._canonical_hybrid_report_fields(
        {
            "active": False,
            "fallback": False,
            "provider": {"provider": "qwen_strict_llm_v3"},
        }
    )

    assert fallback is True
    assert diagnostics["provider"] == "qwen_strict_llm_v3"


def test_fail_closed_provider_row_remains_a_persistable_failed_report(
    tmp_path: Path,
) -> None:
    report = _passing_report()
    row = next(
        row
        for row in report["canonical"]["cases"]
        if row["polarity"] == "hard_negative"
    )
    target_id = row["target_id"]
    row["candidate_ids"] = [
        target_id,
        *[
            evaluator._report_identifier("entry", f"fallback-{index:03d}")
            for index in range(evaluator.MAX_CANDIDATES + 8)
        ],
    ]
    row["target_candidate"] = True
    row["provider_fallback"] = True
    row["hybrid"] = evaluator._project_canonical_hybrid_diagnostics(
        {"active": False, "fallback": True, "provider": {}}
    )
    report["canonical"]["counts"]["provider_fallback_count"] = 1
    report["usage"]["verifier"].update(
        {
            "completed_call_count": 0,
            "failed_call_count": 1,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    report["usage"]["challenger"].update(
        {
            "completed_call_count": 0,
            "failed_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    checks = evaluator.acceptance_checks(report)
    report["acceptance_gate"] = {"passed": all(checks.values()), "checks": checks}
    report["report_fingerprint"] = evidence_fingerprint(report)
    report_path = tmp_path / "failed-report.json"
    evidence_path = tmp_path / "must-not-exist.json"

    emitted = evaluator.persist_report_and_optional_evidence(
        report,
        report_path=report_path,
        evidence_path=evidence_path,
    )

    assert checks["provider_fallback"] is False
    assert emitted is None
    assert report_path.is_file()
    assert not evidence_path.exists()


def test_qwen_identity_is_pinned_to_text_embedding_v3() -> None:
    assert evaluator.validate_qwen_identity(_identity())["model_id"] == (
        "text-embedding-v3"
    )
    wrong = _identity()
    wrong["endpoint"] = "https://example.invalid/embeddings"

    with pytest.raises(ValueError, match="pinned text-embedding-v3"):
        evaluator.validate_qwen_identity(wrong)

    wrong_dimension = _identity()
    wrong_dimension["dimension"] = 8
    with pytest.raises(ValueError, match="pinned text-embedding-v3"):
        evaluator.validate_qwen_identity(wrong_dimension)


def test_passing_report_usage_must_join_exactly_to_provider_rows() -> None:
    report = _passing_report()
    report["usage"]["verifier"]["completed_call_count"] -= 1
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="usage disagrees with canonical rows"):
        evaluator.validate_content_free_promotion_report(report)


def test_passing_report_cannot_understate_embedding_stage_calls() -> None:
    report = _passing_report()
    for stage_usage in report["usage"]["embedding_stages"].values():
        stage_usage.update(
            {
                "completed_call_count": 0,
                "failed_call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "usage_unavailable_count": 0,
            }
        )
    report["usage"]["embedding_stages"]["identity"].update(
        {"completed_call_count": 1, "input_tokens": 1}
    )
    report["usage"]["embedding"].update(
        {"completed_call_count": 1, "input_tokens": 1}
    )
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="embedding stage calls disagree"):
        evaluator.validate_content_free_promotion_report(report)


def test_zero_of_sixty_exact_upper_bound_meets_preregistered_gate() -> None:
    upper = evaluator.one_sided_exact_binomial_upper(0, 60)

    assert upper == pytest.approx(0.0487029133)
    assert upper <= 0.05
    assert evaluator.one_sided_exact_binomial_upper(1, 60) > 0.05


def test_global_dense_recall_uses_one_shared_corpus_and_category_macro() -> None:
    class OneHotEncoder:
        @staticmethod
        def _vector(text: str) -> tuple[float, ...]:
            vector = [0.0] * 164
            match = re.search(r"(?:target-|query )(\d{1,3})", text)
            vector[int(match.group(1)) if match else 163] = 1.0
            return tuple(vector)

        def encode_documents(self, texts, *, call_context=None):
            del call_context
            return tuple(self._vector(text) for text in texts)

        def encode_queries(self, texts, *, call_context=None):
            del call_context
            return tuple(self._vector(text) for text in texts)

    result = evaluator.evaluate_global_dense(
        _dataset(),
        encoder=OneHotEncoder(),
        usage_sink=evaluator.UsageCollector(),
    )

    assert result["eligible_corpus_size"] == 164
    assert result["positive_hits"] == 24
    assert result["candidate_recall_at_20"] == 1.0
    assert result["category_macro_recall_at_20"] == 1.0
    assert len(result["cases"]) == 84


def test_production_evidence_recomputes_the_full_contract() -> None:
    report = _passing_report()

    evidence = evaluator.build_qwen_v2_production_evidence(report)

    assert (
        evidence["promotion_report"]["report_fingerprint"]
        == report["report_fingerprint"]
    )
    assert evidence["holdout"]["file_sha256"] == (evaluator.EXPECTED_HOLDOUT_SHA256)
    assert evidence["production_enablement_allowed"] is True
    assert evidence["one_shot_claim"] == report["one_shot_claim"]
    assert evidence["resolver_pressure_receipt"] == report["resolver_pressure_receipt"]
    assert evidence["inference_profile"] == dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
    assert (
        evidence["resolver"]
        == build_hybrid_decision_contract("qwen3.7-plus")["resolver"]
    )
    assert evidence["report_fingerprint"] == evidence_fingerprint(evidence)

    with pytest.raises(ValueError, match="artifact reference is unsafe"):
        evaluator.build_qwen_v2_production_evidence(
            report, promotion_artifact="../forged-report.json"
        )
    with pytest.raises(ValueError, match="artifact is fixed"):
        evaluator.build_qwen_v2_production_evidence(
            report, promotion_artifact="artifacts/alternate-report.json"
        )

    tampered = json.loads(json.dumps(report))
    tampered["canonical"]["counts"]["hard_negative_target_renders"] = 1
    tampered["canonical"]["metrics"]["hard_negative_one_sided_exact_95_upper"] = (
        evaluator.one_sided_exact_binomial_upper(1, 60)
    )
    tampered["acceptance_gate"]["passed"] = True
    tampered["report_fingerprint"] = evidence_fingerprint(tampered)

    with pytest.raises(ValueError, match="counts disagree"):
        evaluator.build_qwen_v2_production_evidence(tampered)

    resolver_tampered = json.loads(json.dumps(report))
    resolver_tampered["resolver"]["minimum_confidence"] = 0.1
    resolver_tampered["report_fingerprint"] = evidence_fingerprint(resolver_tampered)
    with pytest.raises(ValueError, match="verifier contract mismatch"):
        evaluator.build_qwen_v2_production_evidence(resolver_tampered)

    profile_tampered = json.loads(json.dumps(report))
    profile_tampered["inference_profile"]["enable_thinking"] = True
    profile_tampered["report_fingerprint"] = evidence_fingerprint(profile_tampered)
    with pytest.raises(ValueError, match="protocol contract mismatch"):
        evaluator.build_qwen_v2_production_evidence(profile_tampered)

    pressure_tampered = json.loads(json.dumps(report))
    pressure_tampered["resolver_pressure_receipt"]["counts"]["approved_count"] = 1
    pressure_tampered["report_fingerprint"] = evidence_fingerprint(pressure_tampered)
    with pytest.raises(ValueError, match="receipt safety contract mismatch"):
        evaluator.build_qwen_v2_production_evidence(pressure_tampered)

    pressure_missing = json.loads(json.dumps(report))
    pressure_missing.pop("resolver_pressure_receipt")
    pressure_missing["report_fingerprint"] = evidence_fingerprint(pressure_missing)
    with pytest.raises(ValueError, match="mismatch"):
        evaluator.build_qwen_v2_production_evidence(pressure_missing)

    one_shot_tampered = json.loads(json.dumps(report))
    one_shot_tampered["one_shot_claim"]["output_artifact"] = "artifacts/replay.json"
    one_shot_tampered["report_fingerprint"] = evidence_fingerprint(one_shot_tampered)
    with pytest.raises(ValueError, match="receipt identity mismatch"):
        evaluator.build_qwen_v2_production_evidence(one_shot_tampered)


def test_production_evidence_cannot_bypass_pressure_release_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _passing_report()
    monkeypatch.setattr(
        evaluator,
        "HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT",
        "",
    )

    with pytest.raises(ValueError, match="not release-allowlisted"):
        evaluator.validate_promotion_report(report)
    with pytest.raises(ValueError, match="not release-allowlisted"):
        evaluator.build_qwen_v2_production_evidence(report)


def test_production_gate_requires_actual_canonical_candidate_recall() -> None:
    report = _passing_report()
    positive_rows = [
        row for row in report["canonical"]["cases"] if row["polarity"] == "positive"
    ]
    for row in positive_rows[:3]:
        row["candidate_ids"] = []
        row["admitted_ids"] = []
        row["rendered_ids"] = []
        row["target_candidate"] = False
        row["target_admitted"] = False
        row["target_rendered"] = False
        row["target_reason_codes"] = []
        row["hybrid"] = _complete_projected_hybrid_diagnostics(
            candidate_count=0,
            preliminary_accepted_count=0,
        )
    counts = report["canonical"]["counts"]
    counts["candidate_positive_hits"] = 21
    counts["admitted_positive_hits"] = 21
    counts["rendered_positive_hits"] = 21
    metrics = report["canonical"]["metrics"]
    metrics["canonical_candidate_positive_recall"] = 21 / 24
    metrics["admitted_positive_recall"] = 21 / 24
    metrics["rendered_positive_recall"] = 21 / 24
    metrics["rendered_precision"] = 1.0
    _synchronize_model_usage(report)
    checks = evaluator.acceptance_checks(report)
    report["acceptance_gate"] = {"passed": all(checks.values()), "checks": checks}
    report["report_fingerprint"] = evidence_fingerprint(report)

    assert checks["rendered_positive_recall"] is True
    assert checks["canonical_candidate_positive_recall"] is False
    with pytest.raises(ValueError, match="failing report"):
        evaluator.build_qwen_v2_production_evidence(report)


@pytest.mark.parametrize(
    "reason_code",
    ("full_path_match", "resolver_input_budget_rejected"),
)
def test_report_schema_allows_bounded_runtime_reason_code(reason_code: str) -> None:
    report = _passing_report()
    positive = next(
        row for row in report["canonical"]["cases"] if row["polarity"] == "positive"
    )
    positive["target_reason_codes"].insert(0, reason_code)
    report["report_fingerprint"] = evidence_fingerprint(report)

    validated = evaluator.validate_promotion_report(report)

    assert validated["report_fingerprint"] == report["report_fingerprint"]


def test_production_gate_catches_hard_negative_admission_hidden_before_render() -> None:
    report = _passing_report()
    row = next(
        row
        for row in report["canonical"]["cases"]
        if row["polarity"] == "hard_negative"
    )
    target_id = row["target_id"]
    row["candidate_ids"] = [target_id]
    row["admitted_ids"] = [target_id]
    row["target_candidate"] = True
    row["target_admitted"] = True
    row["target_reason_codes"] = ["hybrid_semantic_admission"]
    row["hybrid"] = _complete_projected_hybrid_diagnostics(
        candidate_count=1,
        preliminary_accepted_count=1,
    )
    counts = report["canonical"]["counts"]
    counts["false_positive_admissions"] = 1
    counts["hard_negative_target_admissions"] = 1
    metrics = report["canonical"]["metrics"]
    metrics["admitted_precision"] = 24 / 25
    metrics["hard_negative_target_admission_rate"] = 1 / 60
    metrics["hard_negative_admission_one_sided_exact_95_upper"] = (
        evaluator.one_sided_exact_binomial_upper(1, 60)
    )
    _synchronize_model_usage(report)
    checks = evaluator.acceptance_checks(report)
    report["acceptance_gate"] = {"passed": all(checks.values()), "checks": checks}
    report["report_fingerprint"] = evidence_fingerprint(report)

    assert row["target_rendered"] is False
    assert checks["hard_negative_target_render_count"] is True
    assert checks["hard_negative_target_admission_count"] is False
    with pytest.raises(ValueError, match="failing report"):
        evaluator.build_qwen_v2_production_evidence(report)


def test_canonical_leakage_counts_must_recompute_from_case_rows() -> None:
    report = _passing_report()
    report["canonical"]["counts"]["lifecycle_leakage_count"] = 1
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="counts disagree"):
        evaluator.build_qwen_v2_production_evidence(report)


def test_failed_report_is_immutable_and_emits_no_production_evidence(
    tmp_path: Path,
) -> None:
    report = _passing_report()
    positive_rows = [
        row for row in report["canonical"]["cases"] if row["polarity"] == "positive"
    ]
    for row in positive_rows[:6]:
        row["rendered_ids"] = []
        row["target_rendered"] = False
    report["canonical"]["counts"]["rendered_positive_hits"] = 18
    report["canonical"]["metrics"]["rendered_positive_recall"] = 18 / 24
    checks = evaluator.acceptance_checks(report)
    report["acceptance_gate"] = {"passed": all(checks.values()), "checks": checks}
    assert report["acceptance_gate"]["passed"] is False
    report["report_fingerprint"] = evidence_fingerprint(report)
    report_path = tmp_path / "report.json"
    evidence_path = tmp_path / "evidence.json"

    emitted = evaluator.persist_report_and_optional_evidence(
        report,
        report_path=report_path,
        evidence_path=evidence_path,
    )

    assert emitted is None
    assert report_path.is_file()
    assert not evidence_path.exists()
    with pytest.raises(FileExistsError, match="already exists"):
        evaluator.persist_report_and_optional_evidence(
            report,
            report_path=report_path,
            evidence_path=evidence_path,
        )


@pytest.mark.parametrize("nested_target", ("holdout", "acceptance_gate"))
def test_report_is_rejected_before_write_when_nested_schema_can_carry_text(
    tmp_path: Path,
    nested_target: str,
) -> None:
    report = _passing_report()
    report[nested_target]["note"] = "sealed query or memory text"
    report["report_fingerprint"] = evidence_fingerprint(report)
    report_path = tmp_path / "report.json"
    evidence_path = tmp_path / "evidence.json"

    with pytest.raises(ValueError, match="mismatch"):
        evaluator.persist_report_and_optional_evidence(
            report,
            report_path=report_path,
            evidence_path=evidence_path,
        )

    assert report_path.exists() is False
    assert evidence_path.exists() is False


def test_report_identifiers_are_content_free_hashes() -> None:
    report = _passing_report()
    dense_rows = report["global_dense"]["cases"]
    canonical_rows = report["canonical"]["cases"]

    for row in [*dense_rows, *canonical_rows]:
        assert re.fullmatch(r"[0-9a-f]{64}", row["case_id"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["target_id"])
    for row in canonical_rows:
        for field in (
            "candidate_ids",
            "admitted_ids",
            "rendered_ids",
            "foreign_ids",
            "lifecycle_leakage_ids",
            "safety_leakage_ids",
            "forbidden_leakage_ids",
        ):
            assert all(re.fullmatch(r"[0-9a-f]{64}", item) for item in row[field])


def test_report_rows_reject_raw_sealed_content_even_when_gate_says_passed() -> None:
    report = _passing_report()
    report["canonical"]["cases"][0]["query"] = "must not be persisted"
    report["report_fingerprint"] = evidence_fingerprint(report)

    with pytest.raises(ValueError, match="leaks sealed content"):
        evaluator.build_qwen_v2_production_evidence(report)
