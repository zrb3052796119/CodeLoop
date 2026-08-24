"""One-shot canonical promotion gate for Qwen-backed Hybrid Memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from minicode.embeddings import (
    OpenAICompatibleEmbeddingEncoder,
    create_openai_compatible_embedding_client,
)
from minicode.memory_hybrid import (
    HYBRID_CHALLENGER_MODE,
    HYBRID_CHALLENGER_PROMPT_VERSION,
    HYBRID_CHALLENGER_SYSTEM_PROMPT,
    HYBRID_CHALLENGER_VETO_REASONS,
    HYBRID_EVIDENCE_SCHEMA_VERSION,
    HYBRID_PROMPT_VERSION,
    HYBRID_PROTOCOL_VERSION,
    HYBRID_QUERY_GATE_VERSION,
    HYBRID_SYSTEM_PROMPT,
    build_hybrid_promotion_verifier_runtime,
    evidence_fingerprint,
)
from minicode.memory_hybrid_runtime import (
    DEFAULT_DENSE_TOP_K,
    DEFAULT_MAX_MODEL_CALLS_PER_TASK,
    MAX_CANDIDATES,
    create_hybrid_candidate_provider,
)
from scripts.memory_hybrid_v2_canonical_evaluator import (
    HOLDOUTS,
    _write_json_atomic,
    evaluate_canonical,
    evaluate_dense_candidates,
    load_corpus,
    verify_frozen_holdout,
)


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_VERSION = "v7-qwen"
HOLDOUT_MANIFEST_SHA256 = (
    "d3bfde05c1652f6498cb529dd69148165da1eaea44cca4e5f1e48a64a454d97e"
)
EXPECTED_QWEN_MODEL = "text-embedding-v3"
EXPECTED_QWEN_ENDPOINT = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
)


def qwen_evaluation_evidence(
    identity: dict[str, Any],
    verifier_model_id: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "evaluation_only": True,
        "model": dict(identity),
        "verifier": {
            "model_id": verifier_model_id,
            "prompt_version": HYBRID_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "minimum_confidence": 0.85,
        },
        "challenger": {
            "model_id": verifier_model_id,
            "prompt_version": HYBRID_CHALLENGER_PROMPT_VERSION,
            "prompt_sha256": hashlib.sha256(
                HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "minimum_confidence": 0.8,
            "mode": HYBRID_CHALLENGER_MODE,
            "veto_reason_codes": sorted(HYBRID_CHALLENGER_VETO_REASONS),
        },
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "max_model_calls_per_task": DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "acceptance_gate": {"passed": True, "evaluation_bootstrap": True},
        "production_enablement_allowed": True,
    }
    payload["report_fingerprint"] = evidence_fingerprint(payload)
    return payload


def build_qwen_production_evidence(report: dict[str, Any]) -> dict[str, Any]:
    gate = report.get("acceptance_gate")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        raise ValueError("a failing report cannot authorize Qwen production use")
    report_fingerprint = report.get("report_fingerprint")
    if not isinstance(report_fingerprint, str) or len(report_fingerprint) != 64:
        raise ValueError("Qwen promotion report fingerprint is invalid")
    payload = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "promotion_report": {
            "artifact": "artifacts/memory-retrieval-hybrid-v7-qwen-canonical.json",
            "report_fingerprint": report_fingerprint,
        },
        "holdout": {
            "version": HOLDOUT_VERSION,
            "manifest_sha256": HOLDOUT_MANIFEST_SHA256,
        },
        "model": dict(report["model"]),
        "verifier": dict(report["verifier"]),
        "challenger": dict(report["challenger"]),
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "dense_top_k": int(report["dense_top_k"]),
        "max_union_candidates": int(report["max_union_candidates"]),
        "max_model_calls_per_task": DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        "acceptance_gate": dict(gate),
        "production_enablement_allowed": True,
    }
    payload["report_fingerprint"] = evidence_fingerprint(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--verifier-model", default="deepseek-chat")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "artifacts"
        / "memory-retrieval-hybrid-v7-qwen-canonical.json",
    )
    parser.add_argument(
        "--production-evidence-output",
        type=Path,
        default=ROOT
        / "artifacts"
        / "memory-retrieval-hybrid-qwen-v1-production-evidence.json",
    )
    parser.add_argument("--acknowledge-one-shot", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_one_shot:
        raise SystemExit("Qwen canonical holdout requires --acknowledge-one-shot")
    if args.output.exists() or args.production_evidence_output.exists():
        raise SystemExit("Qwen canonical evidence already exists; evaluation is one-shot")

    fixture_root, expected_manifest = HOLDOUTS[HOLDOUT_VERSION]
    frozen = verify_frozen_holdout(fixture_root, expected_manifest)
    if not frozen["matches"] or expected_manifest != HOLDOUT_MANIFEST_SHA256:
        raise SystemExit("frozen v7 Qwen holdout verification failed")

    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_model_adapter

    apply_env_file([args.env_file])
    client = create_openai_compatible_embedding_client(ROOT)
    if client is None:
        raise SystemExit("Qwen embedding configuration is unavailable")
    encoder = OpenAICompatibleEmbeddingEncoder(client, provider="qwen")
    identity = encoder.identity
    if (
        identity.get("model_id") != EXPECTED_QWEN_MODEL
        or identity.get("endpoint") != EXPECTED_QWEN_ENDPOINT
    ):
        raise SystemExit("Qwen promotion requires the pinned DashScope model identity")

    runtime = build_hybrid_promotion_verifier_runtime(
        dict(load_runtime_config(ROOT), model=args.verifier_model)
    )
    model = create_model_adapter(args.verifier_model, None, runtime)
    dataset, entries = load_corpus(fixture_root)
    evaluation = qwen_evaluation_evidence(identity, args.verifier_model)

    def provider_factory(**kwargs):
        return create_hybrid_candidate_provider(
            **kwargs,
            embedding_client_factory=lambda _workspace: client,
        )

    with tempfile.TemporaryDirectory(prefix="minicode-hybrid-qwen-canonical-") as temp:
        temporary_root = Path(temp)
        evidence_path = temporary_root / "evaluation-evidence.json"
        evidence_path.write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        dense = evaluate_dense_candidates(
            dataset,
            entries,
            encoder=encoder,
        )
        canonical = evaluate_canonical(
            dataset,
            entries,
            model=model,
            model_path=None,
            evidence_path=evidence_path,
            workspace=temporary_root / "workspace",
            hybrid_provider_factory=provider_factory,
            embedding_provider="qwen",
            allow_remote_embedding=True,
        )

    thresholds = dataset["promotion_thresholds"]
    metrics = canonical["metrics"]
    checks = {
        "frozen_holdout": frozen["matches"],
        "candidate_recall_at_20": dense["positive_recall_at_20"]
        >= thresholds["candidate_recall_at_20_min"],
        "post_decision_positive_recall": metrics["post_decision_positive_recall"]
        >= thresholds["post_decision_positive_recall_min"],
        "rendered_positive_recall": metrics["rendered_positive_recall"]
        >= thresholds["rendered_positive_recall_min"],
        "rendered_precision": metrics["rendered_precision"]
        >= thresholds["rendered_precision_min"],
        "hard_negative_render_rate": metrics["hard_negative_render_rate"]
        <= thresholds["hard_negative_render_rate_max"],
        "lifecycle_safety_leakage": metrics["lifecycle_safety_leakage"]
        <= thresholds["lifecycle_safety_leakage_max"],
        "unsafe_indexed_count": metrics["unsafe_indexed_count"] == 0,
        "provider_fallback_count": metrics["provider_fallback_count"] == 0,
    }
    report = {
        "schema_version": "2.0",
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "holdout_version": HOLDOUT_VERSION,
        "dataset": frozen,
        "embedding_provider": "qwen",
        "remote_memory_authorized": True,
        "model": identity,
        "verifier": evaluation["verifier"],
        "challenger": evaluation["challenger"],
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "candidate_corpus_protocol": "global_39_eligible_memories",
        "canonical_corpus_protocol": "case_target_plus_24_shared_background",
        "thresholds": thresholds,
        "dense": dense,
        "canonical": canonical,
        "acceptance_gate": {"passed": all(checks.values()), "checks": checks},
    }
    report["report_fingerprint"] = evidence_fingerprint(report)
    _write_json_atomic(args.output, report)
    if report["acceptance_gate"]["passed"]:
        production_evidence = build_qwen_production_evidence(report)
        _write_json_atomic(args.production_evidence_output, production_evidence)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "production_evidence_output": (
                    str(args.production_evidence_output)
                    if report["acceptance_gate"]["passed"]
                    else None
                ),
                "model": identity,
                "candidate_recall_at_20": dense["positive_recall_at_20"],
                **metrics,
                "acceptance_gate": report["acceptance_gate"],
                "report_fingerprint": report["report_fingerprint"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report["acceptance_gate"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
