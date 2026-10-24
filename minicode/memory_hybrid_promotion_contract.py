"""Immutable public identities for the Qwen Hybrid Memory promotion gate.

This module intentionally contains data only. Both the evaluator and the
production activation path import the same values so an evidence document
cannot choose the dataset, output, or verifier route used to validate itself.
"""

from __future__ import annotations

QWEN_PROMOTION_DATASET_ID = "independent-sealed-v12"
QWEN_PROMOTION_HOLDOUT_SHA256 = (
    "58436efc810ff5f5c0e9e6b98b05a08a3c8fa00de769160a1fb4e666b520c55d"
)
QWEN_PROMOTION_MANIFEST_SHA256 = (
    "3c2e85a4ab3575a083dc1f8a646ea394b238da66b7a7377e427bf80224bb36ea"
)
QWEN_PROMOTION_BACKGROUND_COUNT = 80
QWEN_PROMOTION_CASE_COUNT = 84
QWEN_PROMOTION_POSITIVE_COUNT = 24
QWEN_PROMOTION_HARD_NEGATIVE_COUNT = 60

# Protocol v8.1 (2026-10-22, owner-approved amendment): the verifier lane may
# carry retried-then-recovered schema failures. Attempts that end in a
# model.failed event are acceptable when every case retains a final verdict
# (enforced by the canonical row join) and the per-run failure rate stays at
# or below this bound; challenger and resolver lanes remain zero-failure.
QWEN_PROMOTION_VERIFIER_FAILED_CALL_RATE = 0.05

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
