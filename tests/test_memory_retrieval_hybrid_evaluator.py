from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import jsonschema

from experiments.memory_embedding_adapter import DeterministicFakeEmbeddingAdapter
from scripts import memory_retrieval_hybrid_evaluator as evaluator
from scripts.memory_retrieval_hybrid_evaluator import (
    ARM_NAMES,
    calibrate_configuration,
    load_frozen_config,
    load_phase3b_holdout,
    metrics_for_arm,
    stage_attribution,
    write_frozen_config,
)
from scripts.memory_retrieval_semantic_gap_evaluator import load_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE3A_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_semantic_gap"
PHASE3B_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "memory_retrieval_phase3b_holdout"
BASELINE_PATH = PROJECT_ROOT / "artifacts" / "memory-retrieval-semantic-gap-baseline.json"
REPORT_PATH = PROJECT_ROOT / "artifacts" / "memory-retrieval-hybrid-offline.json"
REPORT_SCHEMA_PATH = PROJECT_ROOT / "artifacts" / "memory-retrieval-hybrid-offline.schema.json"


def _entry(entry_id: str) -> dict:
    return {
        "id": entry_id,
        "scope": "project",
        "category": "testing",
        "content": f"synthetic content {entry_id}",
        "tags": [],
        "domains": [],
        "tier": "long_term",
        "lifecycle_status": "active",
        "safety_status": "safe",
        "approval_status": "approved",
        "curator_locked": False,
        "created_at": 1.0,
        "updated_at": 2.0,
        "usefulness_score": 0.0,
        "source": "phase3b_fixture",
        "metadata": {},
        "provenance": {},
    }


def _case(case_id: str, *, positive: bool) -> dict:
    primary = f"{case_id}-primary"
    return {
        "case_id": case_id,
        "split": "sealed",
        "polarity": "positive" if positive else "hard_negative",
        "category": "semantic" if positive else "contrast",
        "query": "synthetic query",
        "query_language": "en",
        "memory_language": "en",
        "expected_scope": "project" if positive else None,
        "lexical_overlap_class": "low",
        "semantic_relation_type": "equivalence" if positive else "contrast",
        "primary_entry_ids": [primary] if positive else [],
        "allowed_secondary_ids": [],
        "must_exclude_ids": [] if positive else [primary],
        "memories": [_entry(primary)],
    }


def _arm(candidate: list[str], gate: list[str], consolidated: list[str], rendered: list[str]) -> dict:
    return {
        "candidate_ids": candidate,
        "post_gate_ids": gate,
        "post_consolidation_ids": consolidated,
        "rendered_ids": rendered,
        "controller_mode": "standard",
        "budget_skipped_ids": [],
        "consolidation_suppressions": [],
        "gate_decisions": [],
    }


def test_holdout_load_validates_schema_counts_and_freeze() -> None:
    dataset = load_phase3b_holdout(PHASE3B_ROOT)
    assert dataset["freeze"]["matches"] is True
    assert len(dataset["cases"]) == 60
    assert len(dataset["background"]) == 64
    assert all(case["split"] == "independent_holdout" for case in dataset["cases"])
    assert all(case["allowed_secondary_ids"] == [] for case in dataset["cases"])


def test_frozen_configuration_hash_rejects_tampering(tmp_path: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "evaluator_version": "test",
        "synthetic_data": True,
        "calibration_split": "analysis",
        "sealed_or_holdout_case_ids_read": [],
        "selected_configuration": {"method": "rrf"},
        "calibration": {"attempts": []},
    }
    path = tmp_path / "config.json"
    written = write_frozen_config(path, payload)
    assert load_frozen_config(path)["payload_sha256"] == written["payload_sha256"]
    document = json.loads(path.read_text())
    document["selected_configuration"]["method"] = "weighted"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_frozen_config(path)


def test_calibration_uses_analysis_only_and_records_every_attempt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(evaluator, "CALIBRATION_TOP_K_CHOICES", ((20, 20),))
    monkeypatch.setattr(evaluator, "CALIBRATION_RRF_K", (60,))
    monkeypatch.setattr(evaluator, "CALIBRATION_WEIGHTS", ())
    monkeypatch.setattr(evaluator, "CALIBRATION_DENSE_THRESHOLDS", (0.5,))
    monkeypatch.setattr(evaluator, "CALIBRATION_MARGINS", (0.0,))
    monkeypatch.setattr(evaluator, "CALIBRATION_STRUCTURED_BONUSES", (0.0,))
    monkeypatch.setattr(evaluator, "CALIBRATION_LEXICAL_OVERRIDES", (1.0,))
    payload = calibrate_configuration(
        adapter=DeterministicFakeEmbeddingAdapter(dimension=12),
        phase3a_dataset_root=PHASE3A_ROOT,
        phase3a_baseline_path=BASELINE_PATH,
        work_root=tmp_path,
    )
    phase3a = load_dataset(PHASE3A_ROOT)
    analysis_ids = {case["case_id"] for case in phase3a["cases"] if case["split"] == "analysis"}
    sealed_ids = {case["case_id"] for case in phase3a["cases"] if case["split"] == "sealed"}
    calibration = payload["calibration"]
    assert set(calibration["analysis_case_ids"]) == analysis_ids
    assert not set(calibration["analysis_case_ids"]) & sealed_ids
    assert calibration["sealed_case_ids_read"] == []
    assert calibration["phase3b_holdout_case_ids_read"] == []
    assert payload["sealed_or_holdout_case_ids_read"] == []
    assert calibration["attempt_count"] == 2
    assert len(calibration["attempts"]) == 2


def test_arm_metrics_distinguish_candidate_gate_render_and_negative_noise() -> None:
    positive = _case("positive", positive=True)
    negative = _case("negative", positive=False)
    cases = [positive, negative]
    entries = {
        memory["id"]: memory for case in cases for memory in case["memories"]
    }
    results = []
    for case in cases:
        entry_id = case["memories"][0]["id"]
        arm = (
            _arm([entry_id], [entry_id], [entry_id], [entry_id])
            if case["polarity"] == "positive"
            else _arm([entry_id], [], [], [])
        )
        results.append(
            {
                "case_id": case["case_id"],
                "arms": {name: copy.deepcopy(arm) for name in ARM_NAMES},
            }
        )
    metrics = metrics_for_arm(cases, results, ARM_NAMES[-1], entries)
    assert metrics["metrics"]["positive_candidate_recall_at_20"] == 1.0
    assert metrics["metrics"]["post_gate_positive_recall"] == 1.0
    assert metrics["metrics"]["rendered_positive_recall"] == 1.0
    assert metrics["metrics"]["rendered_precision"] == 1.0
    assert metrics["metrics"]["hard_negative_candidate_rate"] == 1.0
    assert metrics["metrics"]["hard_negative_rendered_rate"] == 0.0


def test_stage_attribution_is_unique_and_tracks_hybrid_rescue_and_gate_noise_removal() -> None:
    positive = _case("positive", positive=True)
    negative = _case("negative", positive=False)
    results = []
    for case in (positive, negative):
        entry_id = case["memories"][0]["id"]
        lexical = _arm([], [], [], [])
        dense = _arm([entry_id], [entry_id], [entry_id], [entry_id])
        final = (
            _arm([entry_id], [entry_id], [entry_id], [entry_id])
            if case["polarity"] == "positive"
            else _arm([entry_id], [], [], [])
        )
        arms = {name: copy.deepcopy(final) for name in ARM_NAMES}
        arms["arm_a_frozen_lexical"] = lexical
        arms["arm_b_dense_only"] = dense
        results.append({"case_id": case["case_id"], "arms": arms})
    attribution = stage_attribution([positive, negative], results)
    assert attribution["first_loss_stage_counts"] == {
        "hard_negative_false_candidate": 1,
        "rendered_success": 1,
    }
    assert attribution["dense_only_success"] == 1
    assert attribution["hybrid_rescued"] == 1
    assert attribution["hybrid_introduced_noise"] == 1
    assert attribution["semantic_gate_rescued_noise"] == 1
    assert len(attribution["per_case"]) == 2


def test_real_offline_artifact_schema_fingerprint_model_and_decision() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(report, schema)
    fingerprint = report.pop("report_fingerprint")
    assert evaluator.payload_hash(report) == fingerprint
    assert report["model"]["model_id"] == "Xenova/multilingual-e5-small"
    assert report["model"]["trust_remote_code"] is False
    assert report["security_and_isolation"]["remote_inference_calls"] == 0
    assert report["acceptance_gate"]["decision"] == "fail"
    assert set(report["performance"]["scales"]) == {"100", "500", "1000", "10000"}


def test_real_decision_splits_use_all_arms_and_preserve_safety_invariants() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    for split in ("phase3a_sealed", "phase3b_independent_holdout"):
        dataset = report["datasets"][split]
        assert set(dataset["metrics_by_arm"]) == set(ARM_NAMES)
        final = dataset["metrics_by_arm"][ARM_NAMES[-1]]["metrics"]
        assert final["lifecycle_safety_leakage"] == 0
        assert final["incorrect_consolidation_suppression"] == 0
        assert final["duplicate_rendered_rate"] == 0
        assert final["unresolved_conflict_unsafe_render"] == 0
        assert final["rendered_recorded_feedback_id_disagreement"] == 0
        assert final["post_gate_positive_recall"] < 0.85
        assert final["hard_negative_rendered_rate"] > 0.05
