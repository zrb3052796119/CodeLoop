from __future__ import annotations

import json
from pathlib import Path

from minicode.memory_hybrid import (
    HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT,
    evidence_fingerprint,
)
from scripts.memory_hybrid_qwen_canonical_evaluator import (
    build_qwen_production_evidence,
)


def test_qwen_production_evidence_joins_passing_report_and_frozen_holdout() -> None:
    report = {
        "report_fingerprint": "a" * 64,
        "model": {
            "provider": "qwen",
            "model_id": "text-embedding-v3",
            "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
            "dimension": 1024,
            "representation_version": "memory-structured-v1",
            "canary_version": "embedding-canary-v1",
            "canary_fingerprint": "b" * 64,
        },
        "verifier": {"model_id": "deepseek-chat"},
        "challenger": {"model_id": "deepseek-chat"},
        "dense_top_k": 20,
        "max_union_candidates": 32,
        "acceptance_gate": {"passed": True, "checks": {"all": True}},
    }

    evidence = build_qwen_production_evidence(report)

    assert evidence["holdout"] == {
        "version": "v7-qwen",
        "manifest_sha256": (
            "d3bfde05c1652f6498cb529dd69148165da1eaea44cca4e5f1e48a64a454d97e"
        ),
    }
    assert evidence["promotion_report"]["report_fingerprint"] == "a" * 64
    assert evidence["production_enablement_allowed"] is True
    assert evidence["report_fingerprint"] == evidence_fingerprint(evidence)


def test_accepted_qwen_evidence_is_bound_to_passing_v7_report() -> None:
    root = Path(__file__).resolve().parents[1]
    report = json.loads(
        (
            root / "artifacts" / "memory-retrieval-hybrid-v7-qwen-canonical.json"
        ).read_text(encoding="utf-8")
    )
    evidence = json.loads(
        (
            root
            / "artifacts"
            / "memory-retrieval-hybrid-qwen-v1-production-evidence.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["acceptance_gate"]["passed"] is True
    assert report["acceptance_gate"]["passed"] is True
    assert evidence["holdout"]["version"] == "v7-qwen"
    assert (
        evidence["promotion_report"]["report_fingerprint"]
        == report["report_fingerprint"]
    )
    assert evidence_fingerprint(evidence) == evidence["report_fingerprint"]
    assert (
        evidence["report_fingerprint"]
        == HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT
    )
