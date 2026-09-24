"""Immutable public identities for the Qwen Hybrid Memory promotion gate.

This module intentionally contains data only. Both the evaluator and the
production activation path import the same values so an evidence document
cannot choose the dataset, output, or verifier route used to validate itself.
"""

from __future__ import annotations

QWEN_PROMOTION_DATASET_ID = "independent-sealed-v2"
QWEN_PROMOTION_HOLDOUT_SHA256 = (
    "e7a0a464995ebd1d854ebc5381b6ea7f6cbe13f7339b21e1a7472fccbf2384b9"
)
QWEN_PROMOTION_MANIFEST_SHA256 = (
    "ef83f779fc91d92bbcdceb76e1f45c51b4fbeaefd77cd52f0215322d4359dc38"
)
QWEN_PROMOTION_BACKGROUND_COUNT = 80
QWEN_PROMOTION_CASE_COUNT = 84
QWEN_PROMOTION_POSITIVE_COUNT = 24
QWEN_PROMOTION_HARD_NEGATIVE_COUNT = 60

QWEN_PROMOTION_REPORT_ARTIFACT = (
    "artifacts/memory-retrieval-hybrid-v8-qwen-canonical.json"
)
QWEN_PROMOTION_EVIDENCE_ARTIFACT = (
    "artifacts/memory-retrieval-hybrid-qwen-v2-production-evidence.json"
)
QWEN_PROMOTION_ONE_SHOT_MARKER_NAME = ".hybrid-v7-promotion.consumed.json"

QWEN_EMBEDDING_MODEL_ID = "text-embedding-v3"
QWEN_EMBEDDING_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
QWEN_VERIFIER_MODEL_ID = "qwen3.7-plus"
QWEN_VERIFIER_PROVIDER = "openai-compatible"
QWEN_VERIFIER_BASE_URL = (
    "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
QWEN_VERIFIER_ROUTE_KIND = "dedicated_exact_subagent"
QWEN_EMBEDDING_DIMENSION = 1024
QWEN_PROMOTION_DENSE_TOP_K = 20
QWEN_PROMOTION_MAX_CANDIDATES = 32
QWEN_PROMOTION_MAX_MODEL_CALLS = 8
QWEN_PROMOTION_IMPLEMENTATION_FREEZE_SCHEMA_VERSION = "hybrid-evaluator-freeze-v1"
QWEN_PROMOTION_ACCEPTANCE_CHECK_KEYS = frozenset(
    {
        "one_shot_claim_bound",
        "implementation_freeze_unchanged",
        "runtime_implementation_freeze_unchanged",
        "frozen_holdout",
        "candidate_recall_at_20",
        "category_macro_recall_at_20",
        "canonical_candidate_positive_recall",
        "admitted_precision",
        "rendered_positive_recall",
        "rendered_precision",
        "hard_negative_target_admission_count",
        "hard_negative_admission_one_sided_exact_95_upper",
        "hard_negative_target_render_count",
        "hard_negative_one_sided_exact_95_upper",
        "lifecycle_leakage",
        "safety_leakage",
        "forbidden_leakage",
        "forbidden_indexed",
        "provider_fallback",
        "id_mismatch",
    }
)
QWEN_EMBEDDING_IDENTITY_KEYS = frozenset(
    {
        "provider",
        "model_id",
        "endpoint",
        "dimension",
        "representation_version",
        "canary_version",
        "canary_fingerprint",
    }
)


def qwen_verifier_route_identity() -> dict[str, str]:
    """Return the exact public transport identity used by promotion."""

    return {
        "kind": QWEN_VERIFIER_ROUTE_KIND,
        "provider": QWEN_VERIFIER_PROVIDER,
        "base_url": QWEN_VERIFIER_BASE_URL,
        "model_id": QWEN_VERIFIER_MODEL_ID,
    }


QWEN_PRODUCTION_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "synthetic_data",
        "one_shot_claim",
        "promotion_report",
        "holdout",
        "implementation_freeze",
        "runtime_implementation_freeze",
        "resolver_pressure_receipt",
        "embedding_provider",
        "model",
        "verifier_route",
        "inference_profile",
        "verifier",
        "challenger",
        "resolver",
        "query_gate_version",
        "dense_top_k",
        "max_union_candidates",
        "max_model_calls_per_task",
        "acceptance_gate",
        "production_enablement_allowed",
        "report_fingerprint",
    }
)

QWEN_PROMOTION_HOLDOUT_KEYS = frozenset(
    {
        "version",
        "file_sha256",
        "manifest_sha256",
        "background_count",
        "case_count",
        "positive_count",
        "hard_negative_count",
    }
)
QWEN_PROMOTION_REPORT_REFERENCE_KEYS = frozenset({"artifact", "report_fingerprint"})
