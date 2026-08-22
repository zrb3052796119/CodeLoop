from __future__ import annotations

import json
from pathlib import Path

from minicode.memory_hybrid import (
    HYBRID_ACCEPTED_PROMOTION_FINGERPRINT,
    evidence_fingerprint,
)

from scripts.memory_hybrid_v2_canonical_evaluator import (
    HOLDOUTS,
    case_local_corpus,
    load_corpus,
    verify_frozen_holdout,
)
from scripts.install_memory_hybrid_model import (
    EXPECTED_MODEL_FINGERPRINT,
    build_manifest,
)


def test_v2_holdout_is_still_byte_frozen() -> None:
    verification = verify_frozen_holdout()

    assert verification["matches"] is True
    assert verification["mismatches"] == {}


def test_canonical_precision_uses_only_complete_case_local_judgments() -> None:
    dataset, entries = load_corpus()

    assert len(dataset["cases"]) == 32
    assert len(entries) == 48
    for case in dataset["cases"]:
        target_id = f"{case['case_id']}-memory"
        corpus = case_local_corpus(entries, target_id)
        ids = {entry.id for entry in corpus}

        assert len(corpus) == 17
        assert target_id in ids
        assert len([entry_id for entry_id in ids if entry_id.startswith("v2-bg-")]) == 16
        assert not any(
            entry_id.endswith("-memory") and entry_id != target_id
            for entry_id in ids
        )


def test_v3_holdout_was_frozen_before_its_first_model_run() -> None:
    fixture_root, expected_manifest = HOLDOUTS["v3"]
    verification = verify_frozen_holdout(fixture_root, expected_manifest)
    dataset, entries = load_corpus(fixture_root)

    assert verification["matches"] is True
    assert len(dataset["cases"]) == 24
    assert sum(case["polarity"] == "positive" for case in dataset["cases"]) == 12
    assert sum(case["polarity"] == "hard_negative" for case in dataset["cases"]) == 12
    assert len(entries) == 48
    for case in dataset["cases"]:
        corpus = case_local_corpus(entries, f"{case['case_id']}-memory")
        assert len(corpus) == 25


def test_v4_holdout_is_frozen_for_two_stage_promotion() -> None:
    fixture_root, expected_manifest = HOLDOUTS["v4"]
    verification = verify_frozen_holdout(fixture_root, expected_manifest)
    dataset, entries = load_corpus(fixture_root)

    assert verification["matches"] is True
    assert len(dataset["cases"]) == 20
    assert sum(case["polarity"] == "positive" for case in dataset["cases"]) == 10
    assert sum(case["polarity"] == "hard_negative" for case in dataset["cases"]) == 10
    assert len(entries) == 44
    for case in dataset["cases"]:
        assert len(case_local_corpus(entries, f"{case['case_id']}-memory")) == 25


def test_v5_holdout_is_frozen_for_conflict_veto_promotion() -> None:
    fixture_root, expected_manifest = HOLDOUTS["v5"]
    verification = verify_frozen_holdout(fixture_root, expected_manifest)
    dataset, entries = load_corpus(fixture_root)

    assert verification["matches"] is True
    assert len(dataset["cases"]) == 16
    assert sum(case["polarity"] == "positive" for case in dataset["cases"]) == 8
    assert sum(case["polarity"] == "hard_negative" for case in dataset["cases"]) == 8
    assert len(entries) == 40
    for case in dataset["cases"]:
        assert len(case_local_corpus(entries, f"{case['case_id']}-memory")) == 25


def test_v6_holdout_is_frozen_before_qwen_promotion_run() -> None:
    fixture_root, expected_manifest = HOLDOUTS["v6-qwen"]
    verification = verify_frozen_holdout(fixture_root, expected_manifest)
    dataset, entries = load_corpus(fixture_root)

    assert verification["matches"] is True
    assert len(dataset["cases"]) == 16
    assert sum(case["polarity"] == "positive" for case in dataset["cases"]) == 8
    assert sum(case["polarity"] == "hard_negative" for case in dataset["cases"]) == 8
    assert len(entries) == 40
    for case in dataset["cases"]:
        assert len(case_local_corpus(entries, f"{case['case_id']}-memory")) == 25


def test_v7_holdout_is_frozen_after_transport_only_v6_failure() -> None:
    fixture_root, expected_manifest = HOLDOUTS["v7-qwen"]
    verification = verify_frozen_holdout(fixture_root, expected_manifest)
    dataset, entries = load_corpus(fixture_root)

    assert verification["matches"] is True
    assert len(dataset["cases"]) == 16
    assert sum(case["polarity"] == "positive" for case in dataset["cases"]) == 8
    assert sum(case["polarity"] == "hard_negative" for case in dataset["cases"]) == 8
    assert len(entries) == 40
    for case in dataset["cases"]:
        assert len(case_local_corpus(entries, f"{case['case_id']}-memory")) == 25


def test_accepted_production_evidence_is_bound_to_passing_v5_report() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "memory-retrieval-hybrid-v4-production-evidence.json"
    )
    evidence = json.loads(path.read_text(encoding="utf-8"))

    assert evidence["acceptance_gate"]["passed"] is True
    assert evidence["holdout"]["version"] == "v5"
    assert evidence_fingerprint(evidence) == HYBRID_ACCEPTED_PROMOTION_FINGERPRINT
    assert evidence["report_fingerprint"] == HYBRID_ACCEPTED_PROMOTION_FINGERPRINT


def test_model_installer_manifest_matches_promoted_model_identity() -> None:
    manifest = build_manifest()

    assert manifest["model_fingerprint"] == EXPECTED_MODEL_FINGERPRINT
    assert manifest["model_id"] == "Xenova/multilingual-e5-small"
    assert manifest["trust_remote_code"] is False
    assert manifest["remote_inference"] is False
