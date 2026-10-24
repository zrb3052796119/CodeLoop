"""One-shot, independently sealed promotion gate for Qwen Hybrid Memory v7.

The concrete holdout is intentionally not imported by tests or development
code.  This evaluator accepts exactly one byte-pinned JSON file, validates its
complete public schema before constructing any remote provider, then records
content-free retrieval outcomes.  A failed quality gate writes an immutable
report but can never emit production-enablement evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
import tempfile
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from unittest.mock import patch

from minicode.embeddings import (
    MEMORY_REPRESENTATION_VERSION,
    REMOTE_EMBEDDING_CANARY_VERSION,
    OpenAICompatibleEmbeddingEncoder,
    create_openai_compatible_embedding_client,
)
from minicode.evaluation_one_shot import (
    claim_evaluation_run,
    validate_evaluation_one_shot_receipt,
)
from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
from minicode.memory_hybrid import (
    HYBRID_ALLOWED_REASONS,
    HYBRID_CHALLENGER_ALLOWED_REASONS,
    HYBRID_CHALLENGER_VETO_REASONS,
    HYBRID_EVIDENCE_SCHEMA_VERSION,
    HYBRID_PROMOTION_INFERENCE_PROFILE,
    HYBRID_PROTOCOL_VERSION,
    HYBRID_QUERY_GATE_VERSION,
    HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    HYBRID_RESOLVER_PRESSURE_CASE_COUNT,
    HYBRID_RESOLVER_PRESSURE_HOLDOUT_SHA256,
    HYBRID_RESOLVER_PRESSURE_MANIFEST_SHA256,
    HYBRID_RESOLVER_SUPPORT_KINDS,
    build_hybrid_decision_contract,
    build_hybrid_promotion_verifier_runtime,
    evidence_fingerprint,
)
from minicode.memory_hybrid_freeze import (
    HYBRID_RUNTIME_BEHAVIOR_FILES,
    build_hybrid_runtime_freeze,
)
from minicode.memory_hybrid_promotion_contract import (
    QWEN_EMBEDDING_DIMENSION,
    QWEN_EMBEDDING_ENDPOINT,
    QWEN_EMBEDDING_IDENTITY_KEYS,
    QWEN_EMBEDDING_MODEL_ID,
    QWEN_PRODUCTION_EVIDENCE_KEYS,
    QWEN_PROMOTION_ACCEPTANCE_CHECK_KEYS,
    QWEN_PROMOTION_BACKGROUND_COUNT,
    QWEN_PROMOTION_CASE_COUNT,
    QWEN_PROMOTION_DATASET_ID,
    QWEN_PROMOTION_EVIDENCE_ARTIFACT,
    QWEN_PROMOTION_HARD_NEGATIVE_COUNT,
    QWEN_PROMOTION_VERIFIER_FAILED_CALL_RATE,
    QWEN_PROMOTION_HOLDOUT_KEYS,
    QWEN_PROMOTION_HOLDOUT_SHA256,
    QWEN_PROMOTION_IMPLEMENTATION_FREEZE_SCHEMA_VERSION,
    QWEN_PROMOTION_MANIFEST_SHA256,
    QWEN_PROMOTION_ONE_SHOT_MARKER_NAME,
    QWEN_PROMOTION_POSITIVE_COUNT,
    QWEN_PROMOTION_REPORT_ARTIFACT,
    QWEN_VERIFIER_BASE_URL,
    QWEN_VERIFIER_MODEL_ID,
    QWEN_VERIFIER_PROVIDER,
    qwen_verifier_route_identity,
)
from minicode.memory_hybrid_release import (
    HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT,
)
from minicode.memory_hybrid_runtime import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DENSE_TOP_K,
    DEFAULT_MAX_MODEL_CALLS_PER_TASK,
    MAX_CANDIDATES,
    _entry_document,
    _entry_is_dense_eligible,
    _query_document,
    create_hybrid_candidate_provider,
)
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_retrieval import MemoryRetrievalRequest

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_VERSION = QWEN_PROMOTION_DATASET_ID
DEFAULT_HOLDOUT = (
    ROOT
    / ".repair-work"
    / "memory-retrieval-quality-2026-09-03"
    / HOLDOUT_VERSION
    / "holdout.json"
)
EXPECTED_HOLDOUT_SHA256 = QWEN_PROMOTION_HOLDOUT_SHA256
EXPECTED_MANIFEST_SHA256 = QWEN_PROMOTION_MANIFEST_SHA256
EXPECTED_BACKGROUND_COUNT = QWEN_PROMOTION_BACKGROUND_COUNT
EXPECTED_CASE_COUNT = QWEN_PROMOTION_CASE_COUNT
EXPECTED_POSITIVE_COUNT = QWEN_PROMOTION_POSITIVE_COUNT
EXPECTED_HARD_NEGATIVE_COUNT = QWEN_PROMOTION_HARD_NEGATIVE_COUNT
MAX_HOLDOUT_BYTES = 4_000_000
REFERENCE_TIME = 1_800_000_000.0

EXPECTED_QWEN_MODEL = QWEN_EMBEDDING_MODEL_ID
EXPECTED_QWEN_ENDPOINT = QWEN_EMBEDDING_ENDPOINT
DEFAULT_VERIFIER_MODEL = QWEN_VERIFIER_MODEL_ID
REPORT_SCHEMA_VERSION = "3.3"
REPORT_ARTIFACT = QWEN_PROMOTION_REPORT_ARTIFACT
PRODUCTION_EVIDENCE_ARTIFACT = QWEN_PROMOTION_EVIDENCE_ARTIFACT
ONE_SHOT_MARKER_NAME = QWEN_PROMOTION_ONE_SHOT_MARKER_NAME
DEFAULT_ONE_SHOT_MARKER = DEFAULT_HOLDOUT.parent / ONE_SHOT_MARKER_NAME
DEFAULT_RESOLVER_PRESSURE_REPORT = ROOT / HYBRID_RESOLVER_PRESSURE_ARTIFACT
MAX_RESOLVER_PRESSURE_REPORT_BYTES = 2_000_000
IMPLEMENTATION_FREEZE_SCHEMA_VERSION = (
    QWEN_PROMOTION_IMPLEMENTATION_FREEZE_SCHEMA_VERSION
)
BEHAVIOR_CRITICAL_FILES = (
    "scripts/memory_hybrid_qwen_v2_evaluator.py",
    "scripts/memory_hybrid_resolver_pressure_evaluator.py",
    *HYBRID_RUNTIME_BEHAVIOR_FILES,
)

PROMOTION_THRESHOLDS: dict[str, float] = {
    "candidate_recall_at_20_min": 0.90,
    "category_macro_recall_at_20_min": 0.85,
    "canonical_candidate_positive_recall_min": 0.90,
    "admitted_precision_min": 0.95,
    "rendered_positive_recall_min": 0.80,
    "rendered_precision_min": 0.95,
    "hard_negative_target_admission_count_max": 0.0,
    "hard_negative_admission_one_sided_exact_95_upper_max": 0.05,
    "hard_negative_target_render_count_max": 0.0,
    "hard_negative_one_sided_exact_95_upper_max": 0.05,
    "lifecycle_leakage_count_max": 0.0,
    "safety_leakage_count_max": 0.0,
    "forbidden_leakage_count_max": 0.0,
    "forbidden_indexed_count_max": 0.0,
    "provider_fallback_count_max": 0.0,
    "id_mismatch_count_max": 0.0,
}
_PROMOTION_CHECK_KEYS = QWEN_PROMOTION_ACCEPTANCE_CHECK_KEYS

_TOP_LEVEL_KEYS = frozenset({"background_entries", "cases"})
_CASE_KEYS = frozenset(
    {
        "id",
        "query",
        "current_files",
        "active_domains",
        "entry",
        "expected_relevant",
        "polarity",
        "relation",
    }
)
_ENTRY_KEYS = frozenset({"id", "memory_text", "metadata", "domains", "stored_files"})
_DENSE_ROW_KEYS = frozenset(
    {
        "case_id",
        "target_id",
        "polarity",
        "relation_sha256",
        "target_rank",
        "target_in_top_20",
        "top_20_ids_sha256",
    }
)
_CANONICAL_ROW_KEYS = frozenset(
    {
        "case_id",
        "target_id",
        "polarity",
        "relation_sha256",
        "candidate_ids",
        "admitted_ids",
        "rendered_ids",
        "target_candidate",
        "target_admitted",
        "target_rendered",
        "target_reason_codes",
        "foreign_ids",
        "lifecycle_leakage_ids",
        "safety_leakage_ids",
        "forbidden_leakage_ids",
        "provider_fallback",
        "hybrid",
    }
)
_MODEL_IDENTITY_KEYS = QWEN_EMBEDDING_IDENTITY_KEYS
POST_CLAIM_FAILURE_SCHEMA_VERSION = "hybrid-promotion-failure-v1"
_POST_CLAIM_FAILURE_STAGES = frozenset(
    {
        "embedding_identity",
        "global_dense",
        "canonical_retrieval",
        "report_build",
    }
)
_POST_CLAIM_FAILURE_KINDS = frozenset(
    {
        "interrupted",
        "timeout",
        "provider_or_transport_error",
        "post_claim_validation_error",
        "resource_error",
        "unexpected_error",
    }
)
_POST_CLAIM_FAILURE_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "synthetic_data",
        "one_shot",
        "production_authority",
        "one_shot_claim",
        "holdout",
        "implementation_freeze",
        "runtime_implementation_freeze",
        "resolver_pressure_receipt",
        "embedding_provider",
        "verifier_route",
        "inference_profile",
        "failure_stage",
        "failure_kind",
        "usage",
        "report_fingerprint",
    }
)
_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "synthetic_data",
        "one_shot",
        "one_shot_claim",
        "holdout",
        "implementation_freeze",
        "runtime_implementation_freeze",
        "resolver_pressure_receipt",
        "embedding_provider",
        "remote_memory_authorized",
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
        "candidate_corpus_protocol",
        "canonical_corpus_protocol",
        "thresholds",
        "global_dense",
        "canonical",
        "usage",
        "acceptance_gate",
        "report_fingerprint",
    }
)
_HYBRID_DIAGNOSTIC_KEYS = frozenset(
    {
        "provider",
        "cache_hit",
        "dense_candidate_count",
        "dense_lane_count",
        "lexical_rescue_count",
        "candidate_count",
        "adjudicated_count",
        "deterministic_accepted_count",
        "deterministic_rejected_count",
        "query_gate_rejected_count",
        "preliminary_accepted_count",
        "challenged_count",
        "challenger_accepted_count",
        "resolver_attempted_count",
        "resolver_input_rejected_count",
        "resolver_disagreement_count",
        "resolver_accepted_count",
        "semantic_accepted_count",
        "accepted_count",
        "exact_gate_rejected_count",
        "candidate_budget_rejected_count",
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
        "deterministic_rejection_reason_codes",
    }
)
_EXPECTED_HYBRID_DIAGNOSTIC_PROVIDER = "qwen_strict_llm_v3"
_HYBRID_INTEGER_FIELDS = (
    "dense_candidate_count",
    "dense_lane_count",
    "lexical_rescue_count",
    "candidate_count",
    "adjudicated_count",
    "deterministic_accepted_count",
    "deterministic_rejected_count",
    "query_gate_rejected_count",
    "preliminary_accepted_count",
    "challenged_count",
    "challenger_accepted_count",
    "resolver_attempted_count",
    "resolver_input_rejected_count",
    "resolver_disagreement_count",
    "resolver_accepted_count",
    "semantic_accepted_count",
    "accepted_count",
    "exact_gate_rejected_count",
    "candidate_budget_rejected_count",
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
)
_PRESSURE_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "synthetic_data",
        "one_shot",
        "one_shot_claim",
        "production_authority",
        "holdout",
        "implementation_freeze",
        "inference_profile",
        "verifier_route",
        "verifier",
        "challenger",
        "resolver",
        "resolver_pressure",
        "acceptance_gate",
        "report_fingerprint",
    }
)
_PRESSURE_CASE_KEYS = frozenset(
    {
        "case_id",
        "entry_id",
        "relation_sha256",
        "contested_reason",
        "primary_reason",
        "decision",
        "confidence",
        "support_kind",
        "disqualifier",
        "query_span",
        "memory_span",
        "query_segment_kind",
        "memory_segment_kind",
        "approved",
        "protocol_failure",
    }
)
_PRESSURE_RECEIPT_KEYS = frozenset(
    {
        "artifact",
        "report_fingerprint",
        "holdout_sha256",
        "manifest_sha256",
        "counts",
        "approved_one_sided_exact_95_upper",
    }
)
_HEX_64 = frozenset("0123456789abcdef")
_SAFE_REPORT_ID = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_REASON_CODE = re.compile(r"[a-z0-9][a-z0-9_:-]{0,127}\Z")
_LEXICAL_REASON_CODES = frozenset(
    {
        "phrase_match",
        "exact_tag_match",
        "file_match",
        "full_path_match",
        "multiple_informative_terms",
        "high_token_coverage",
        "domain_with_lexical_evidence",
        "category_intent_with_object",
        "project_exact_tag_object",
        "explicit_user_context_object",
        "failure_signature_match",
        "insufficient_relevance_evidence",
        "domain_match",
        "category_intent_match",
    }
)
_DETERMINISTIC_REASON_CODES = frozenset(
    {
        "path_conflict",
        "obsolete_configuration",
        "operation_order_conflict",
        "negation_conflict",
        "action_polarity_conflict",
        "network_exposure_conflict",
        "confirmation_policy_conflict",
        "proven_rename",
    }
)
_TARGET_REASON_CODES = frozenset(
    {
        *_LEXICAL_REASON_CODES,
        "hybrid_query_underspecified",
        "hybrid_ineligible_exact_gate",
        "hybrid_candidate_budget",
        "hybrid_semantic_admission",
        "hybrid_semantic_rejection",
        "challenge_below_confidence",
        "challenge_missing_consent",
        "resolver_proven_relevant",
        "resolver_below_confidence",
        "resolver_support_not_authorized",
        "resolver_unverified_recovery",
        "resolver_input_budget_rejected",
        *(f"deterministic_{reason}" for reason in _DETERMINISTIC_REASON_CODES),
        *(f"semantic_{reason}" for reason in HYBRID_ALLOWED_REASONS),
        *(f"challenge_{reason}" for reason in HYBRID_CHALLENGER_ALLOWED_REASONS),
        *(f"resolver_{reason}" for reason in HYBRID_CHALLENGER_VETO_REASONS),
    }
)


class PromotionEvaluationRunFailed(RuntimeError):
    """Content-free carrier for a consumed one-shot evaluation failure."""

    def __init__(self, failure_report: dict[str, Any]) -> None:
        self.failure_report = validate_post_claim_failure_report(failure_report)
        super().__init__(
            "sealed promotion evaluation failed after one-shot claim: "
            f"{self.failure_report['failure_stage']}/"
            f"{self.failure_report['failure_kind']}"
        )


@dataclass(frozen=True)
class FrozenHoldout:
    """Validated sealed data plus content-free identity metadata."""

    dataset: dict[str, Any]
    file_sha256: str
    manifest_sha256: str

    @property
    def summary(self) -> dict[str, Any]:
        cases = self.dataset["cases"]
        return {
            "version": HOLDOUT_VERSION,
            "file_sha256": self.file_sha256,
            "manifest_sha256": self.manifest_sha256,
            "background_count": len(self.dataset["background_entries"]),
            "case_count": len(cases),
            "positive_count": sum(case["polarity"] == "positive" for case in cases),
            "hard_negative_count": sum(
                case["polarity"] == "hard_negative" for case in cases
            ),
        }


class UsageCollector:
    """Collect bounded provider-neutral usage without recording content."""

    _EMBEDDING_STAGES = (
        "identity",
        "global_documents",
        "global_query",
        "canonical_documents",
        "canonical_query",
        "unattributed",
    )

    def __init__(self) -> None:
        self._completed: dict[str, int] = defaultdict(int)
        self._failed: dict[str, int] = defaultdict(int)
        self._input_tokens: dict[str, int] = defaultdict(int)
        self._output_tokens: dict[str, int] = defaultdict(int)
        self._usage_unavailable: dict[str, int] = defaultdict(int)
        self._embedding_completed: dict[str, int] = defaultdict(int)
        self._embedding_failed: dict[str, int] = defaultdict(int)
        self._embedding_input_tokens: dict[str, int] = defaultdict(int)
        self._embedding_output_tokens: dict[str, int] = defaultdict(int)
        self._embedding_usage_unavailable: dict[str, int] = defaultdict(int)

    @staticmethod
    def _lane(purpose: object) -> str:
        value = str(purpose or "")
        if "embedding" in value:
            return "embedding"
        if "resolver" in value:
            return "resolver"
        if "challenger" in value:
            return "challenger"
        if "verifier" in value:
            return "verifier"
        return "other"

    @classmethod
    def _embedding_stage(cls, purpose: object) -> str:
        value = str(purpose or "")
        if "embedding_identity" in value:
            return "identity"
        if "embedding_global_documents" in value:
            return "global_documents"
        if "embedding_global_query" in value:
            return "global_query"
        if "embedding_documents" in value:
            return "canonical_documents"
        if "embedding_query" in value:
            return "canonical_query"
        return "unattributed"

    def emit(
        self,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        del step
        body = dict(payload or {})
        purpose = body.get("purpose")
        lane = self._lane(purpose)
        embedding_stage = (
            self._embedding_stage(purpose) if lane == "embedding" else None
        )
        if event_type == "model.failed":
            self._failed[lane] += 1
            if embedding_stage is not None:
                self._embedding_failed[embedding_stage] += 1
            return
        if event_type != "model.completed":
            return
        self._completed[lane] += 1
        if embedding_stage is not None:
            self._embedding_completed[embedding_stage] += 1
        usage = body.get("usage")
        if not isinstance(usage, Mapping) or usage.get("source") == "unavailable":
            self._usage_unavailable[lane] += 1
            if embedding_stage is not None:
                self._embedding_usage_unavailable[embedding_stage] += 1
            return
        input_tokens = usage.get("inputTokens")
        output_tokens = usage.get("outputTokens")
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
            self._input_tokens[lane] += max(0, input_tokens)
            if embedding_stage is not None:
                self._embedding_input_tokens[embedding_stage] += max(0, input_tokens)
        if isinstance(output_tokens, int) and not isinstance(output_tokens, bool):
            self._output_tokens[lane] += max(0, output_tokens)
            if embedding_stage is not None:
                self._embedding_output_tokens[embedding_stage] += max(
                    0, output_tokens
                )

    def snapshot(self) -> dict[str, Any]:
        lanes = ("embedding", "verifier", "challenger", "resolver", "other")
        usage = {
            lane: {
                "completed_call_count": self._completed[lane],
                "failed_call_count": self._failed[lane],
                "input_tokens": self._input_tokens[lane],
                "output_tokens": self._output_tokens[lane],
                "usage_unavailable_count": self._usage_unavailable[lane],
            }
            for lane in lanes
        }
        usage["embedding_stages"] = {
            stage: {
                "completed_call_count": self._embedding_completed[stage],
                "failed_call_count": self._embedding_failed[stage],
                "input_tokens": self._embedding_input_tokens[stage],
                "output_tokens": self._embedding_output_tokens[stage],
                "usage_unavailable_count": self._embedding_usage_unavailable[stage],
            }
            for stage in self._EMBEDDING_STAGES
        }
        return usage


class _ObservedEmbeddingClient:
    """Attach the evaluator's usage sink to every embedding call, including canaries."""

    def __init__(self, client: Any, sink: UsageCollector) -> None:
        self._client = client
        self._sink = sink

    @property
    def model(self) -> str:
        return str(getattr(self._client, "model", ""))

    @property
    def endpoint(self) -> str:
        return str(getattr(self._client, "endpoint", ""))

    def embed(
        self,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> Any:
        context = dict(call_context or {})
        context.setdefault("event_sink", self._sink)
        context.setdefault("purpose", "memory_hybrid_embedding_identity")
        return self._client.embed(texts, call_context=context)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_implementation_freeze(root: Path = ROOT) -> dict[str, Any]:
    """Hash the evaluator and every behavior-critical dependency.

    This local source certificate is deliberately independent of the future
    production-baseline manifest.  It is computed before the sealed dataset is
    read and therefore cannot adapt its policy to revealed cases.
    """
    resolved_root = Path(root).resolve(strict=True)
    rows: list[dict[str, str]] = []
    for relative in BEHAVIOR_CRITICAL_FILES:
        path = resolved_root / relative
        resolved = path.resolve(strict=True)
        if (
            path.is_symlink()
            or not path.is_file()
            or resolved_root not in resolved.parents
        ):
            raise ValueError(
                f"behavior-critical source is missing or unsafe: {relative}"
            )
        rows.append({"path": relative, "sha256": _sha256_file(path)})
    body = {
        "schema_version": IMPLEMENTATION_FREEZE_SCHEMA_VERSION,
        "file_count": len(rows),
        "files": rows,
    }
    return {
        **body,
        "aggregate_sha256": hashlib.sha256(
            _stable_json(body).encode("utf-8")
        ).hexdigest(),
    }


def _parse_json_without_duplicate_keys(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_non_finite(value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_non_finite,
        )
    except (RecursionError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("sealed holdout is not valid UTF-8 JSON") from exc


def _safe_json_artifact_reference(value: object) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("resolver pressure artifact reference is unsafe")
    artifact = PurePosixPath(value)
    if (
        not artifact.parts
        or artifact.is_absolute()
        or ".." in artifact.parts
        or artifact.suffix != ".json"
    ):
        raise ValueError("resolver pressure artifact reference is unsafe")
    return artifact.as_posix()


def validate_promotion_one_shot_claim(value: Any) -> dict[str, int | str]:
    """Bind a promotion report to the only authorized holdout/output pair."""

    return validate_evaluation_one_shot_receipt(
        value,
        dataset_id=HOLDOUT_VERSION,
        holdout_sha256=EXPECTED_HOLDOUT_SHA256,
        output_artifact=REPORT_ARTIFACT,
        marker_name=ONE_SHOT_MARKER_NAME,
    )


def _validate_pressure_span(value: Any, field: str) -> None:
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError(f"resolver pressure {field} is malformed")
    start = value["start"]
    end = value["end"]
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not 0 <= start < end <= 100_000
    ):
        raise ValueError(f"resolver pressure {field} is malformed")


def validate_resolver_pressure_report(report: Any) -> dict[str, Any]:
    """Validate the independent resolver-pressure authority without its holdout."""
    if not isinstance(report, dict) or set(report) != _PRESSURE_REPORT_KEYS:
        raise ValueError("resolver pressure report schema mismatch")
    fingerprint = report.get("report_fingerprint")
    if not _is_sha256(fingerprint) or fingerprint != evidence_fingerprint(report):
        raise ValueError("resolver pressure report fingerprint mismatch")

    # Lazy import avoids a module cycle: the pressure runner reuses this file's
    # exact-subagent adapter constructor. Its freeze reads source only and never
    # opens the resolver or 84-case sealed holdouts.
    from scripts import memory_hybrid_resolver_pressure_evaluator as pressure

    validate_evaluation_one_shot_receipt(
        report.get("one_shot_claim"),
        dataset_id=pressure.DATASET_ID,
        holdout_sha256=pressure.EXPECTED_HOLDOUT_SHA256,
        output_artifact=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        marker_name=pressure.ONE_SHOT_MARKER_NAME,
    )

    if (
        report.get("schema_version") != pressure.REPORT_SCHEMA_VERSION
        or report.get("protocol_version") != HYBRID_PROTOCOL_VERSION
        or report.get("synthetic_data") is not True
        or report.get("one_shot") is not True
        or report.get("production_authority") is not False
        or report.get("inference_profile") != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
        or report.get("implementation_freeze") != pressure.implementation_freeze()
    ):
        raise ValueError("resolver pressure report authority mismatch")

    verifier = report.get("verifier")
    challenger = report.get("challenger")
    resolver = report.get("resolver")
    if not isinstance(verifier, dict):
        raise TypeError("resolver pressure verifier contract is missing")
    model_id = verifier.get("model_id")
    if model_id != DEFAULT_VERIFIER_MODEL:
        raise ValueError("resolver pressure verifier model is invalid")
    expected_decision_contract = build_hybrid_decision_contract(model_id)
    if {
        "verifier": verifier,
        "challenger": challenger,
        "resolver": resolver,
    } != expected_decision_contract:
        raise ValueError("resolver pressure decision contract mismatch")
    if report.get("verifier_route") != qwen_verifier_route_identity():
        raise ValueError("resolver pressure verifier route mismatch")

    holdout = report.get("holdout")
    if not isinstance(holdout, dict) or set(holdout) != {
        "dataset_id",
        "holdout_sha256",
        "manifest_sha256",
        "case_count",
    }:
        raise ValueError("resolver pressure holdout receipt is malformed")
    if (
        holdout.get("dataset_id") != pressure.DATASET_ID
        or holdout.get("holdout_sha256") != pressure.EXPECTED_HOLDOUT_SHA256
        or holdout.get("manifest_sha256") != pressure.EXPECTED_MANIFEST_SHA256
        or holdout.get("case_count") != pressure.EXPECTED_CASE_COUNT
    ):
        raise ValueError("resolver pressure holdout receipt is invalid")

    result = report.get("resolver_pressure")
    if not isinstance(result, dict) or set(result) != {
        "counts",
        "metrics",
        "usage",
        "cases",
    }:
        raise ValueError("resolver pressure result schema mismatch")
    counts = result["counts"]
    metrics = result["metrics"]
    usage = result["usage"]
    cases = result["cases"]
    if not isinstance(counts, dict) or set(counts) != {
        "case_count",
        "raw_relevant_count",
        "approved_count",
        "protocol_failure_batches",
        "protocol_failure_cases",
    }:
        raise ValueError("resolver pressure counts are malformed")
    if any(
        isinstance(counts[field], bool) or not isinstance(counts[field], int)
        for field in counts
    ):
        raise ValueError("resolver pressure counts are malformed")
    expected_batch_count = math.ceil(
        pressure.EXPECTED_CASE_COUNT / pressure.DEFAULT_BATCH_SIZE
    )
    if (
        counts.get("case_count") != pressure.EXPECTED_CASE_COUNT
        or not 0 <= counts["raw_relevant_count"] <= counts["case_count"]
        or not 0 <= counts["approved_count"] <= counts["raw_relevant_count"]
        or not 0 <= counts["protocol_failure_batches"] <= expected_batch_count
        or not 0 <= counts["protocol_failure_cases"] <= counts["case_count"]
        or counts["protocol_failure_cases"]
        != counts["protocol_failure_batches"] * pressure.DEFAULT_BATCH_SIZE
    ):
        raise ValueError("resolver pressure counts are inconsistent")
    if not isinstance(metrics, dict) or set(metrics) != {
        "raw_relevant_rate",
        "approved_rate",
        "approved_one_sided_exact_95_upper",
    }:
        raise ValueError("resolver pressure metrics are malformed")
    upper = metrics.get("approved_one_sided_exact_95_upper")
    expected_upper = one_sided_exact_binomial_upper(
        counts["approved_count"], pressure.EXPECTED_CASE_COUNT
    )
    if (
        metrics.get("raw_relevant_rate")
        != counts["raw_relevant_count"] / pressure.EXPECTED_CASE_COUNT
        or metrics.get("approved_rate")
        != counts["approved_count"] / pressure.EXPECTED_CASE_COUNT
        or isinstance(upper, bool)
        or not isinstance(upper, (int, float))
        or not math.isfinite(float(upper))
        or not 0.0 <= float(upper) <= 1.0
        or float(upper) != expected_upper
    ):
        raise ValueError("resolver pressure metrics are inconsistent")
    if not isinstance(usage, dict) or set(usage) != {
        "model_call_count",
        "input_tokens",
        "output_tokens",
    }:
        raise ValueError("resolver pressure usage is malformed")
    for field, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"resolver pressure {field} is invalid")
    if usage["model_call_count"] != expected_batch_count:
        raise ValueError("resolver pressure model-call count is invalid")
    if not isinstance(cases, list) or len(cases) != pressure.EXPECTED_CASE_COUNT:
        raise ValueError("resolver pressure case rows are incomplete")

    seen_cases: set[str] = set()
    seen_entries: set[str] = set()
    recomputed_raw_relevant = 0
    recomputed_approved = 0
    recomputed_protocol_failures = 0
    for row in cases:
        if not isinstance(row, dict) or set(row) != _PRESSURE_CASE_KEYS:
            raise ValueError("resolver pressure case row schema mismatch")
        case_id = row["case_id"]
        entry_id = row["entry_id"]
        if (
            not isinstance(case_id, str)
            or _SAFE_REPORT_ID.fullmatch(case_id) is None
            or case_id in seen_cases
            or not isinstance(entry_id, str)
            or _SAFE_REPORT_ID.fullmatch(entry_id) is None
            or entry_id in seen_entries
            or not _is_sha256(row["relation_sha256"])
            or row["contested_reason"] not in HYBRID_CHALLENGER_VETO_REASONS
            or row["primary_reason"] not in HYBRID_ALLOWED_REASONS
            or row["decision"] not in {"relevant", "irrelevant"}
            or row["support_kind"] not in HYBRID_RESOLVER_SUPPORT_KINDS
            or row["disqualifier"] not in HYBRID_CHALLENGER_VETO_REASONS
            or row["query_segment_kind"]
            not in {"query.task", "query.current_files", "query.active_domains"}
            or row["memory_segment_kind"]
            not in {
                "memory.content",
                "memory.category",
                "memory.tags",
                "memory.domains",
                "memory.metadata",
            }
            or not isinstance(row["approved"], bool)
            or not isinstance(row["protocol_failure"], bool)
        ):
            raise ValueError("resolver pressure case row is invalid")
        confidence = row["confidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ValueError("resolver pressure confidence is invalid")
        _validate_pressure_span(row["query_span"], "query span")
        _validate_pressure_span(row["memory_span"], "memory span")
        if row["protocol_failure"] and (
            row["decision"] != "irrelevant"
            or float(row["confidence"]) != 0.0
            or row["support_kind"] != "none"
            or row["disqualifier"] != row["contested_reason"]
            or row["approved"] is not False
        ):
            raise ValueError(
                "resolver pressure protocol failure row is not fail-closed"
            )
        seen_cases.add(case_id)
        seen_entries.add(entry_id)
        recomputed_raw_relevant += int(row["decision"] == "relevant")
        recomputed_approved += int(row["approved"])
        recomputed_protocol_failures += int(row["protocol_failure"])
    if (
        recomputed_raw_relevant != counts["raw_relevant_count"]
        or recomputed_approved != counts["approved_count"]
        or recomputed_protocol_failures != counts["protocol_failure_cases"]
    ):
        raise ValueError("resolver pressure rows disagree with reported counts")

    expected_checks = {
        "one_shot_claim_bound": True,
        "sealed_case_count": True,
        "raw_relevant_count_zero": counts["raw_relevant_count"] == 0,
        "approved_count_zero": counts["approved_count"] == 0,
        "protocol_failure_batches_zero": counts["protocol_failure_batches"] == 0,
        "protocol_failure_cases_zero": counts["protocol_failure_cases"] == 0,
        "approved_upper_bound_at_most_0_05": float(upper) <= 0.05,
        "exact_batch_call_count": True,
        "content_free_report": True,
    }
    gate = report.get("acceptance_gate")
    if (
        not isinstance(gate, dict)
        or set(gate) != {"passed", "checks"}
        or not isinstance(gate.get("passed"), bool)
        or gate.get("passed") != all(expected_checks.values())
        or gate.get("checks") != expected_checks
        or _contains_sealed_content_key(report)
    ):
        raise ValueError("resolver pressure acceptance gate is invalid")
    return report


def validate_resolver_pressure_receipt(value: Any) -> dict[str, Any]:
    """Validate the content-free subset bound into promotion artifacts."""
    if not isinstance(value, dict) or set(value) != _PRESSURE_RECEIPT_KEYS:
        raise ValueError("resolver pressure receipt schema mismatch")
    artifact = _safe_json_artifact_reference(value.get("artifact"))
    counts = value.get("counts")
    if not isinstance(counts, dict) or set(counts) != {
        "case_count",
        "raw_relevant_count",
        "approved_count",
    }:
        raise ValueError("resolver pressure receipt counts are malformed")
    upper = value.get("approved_one_sided_exact_95_upper")
    if (
        artifact != HYBRID_RESOLVER_PRESSURE_ARTIFACT
        or not _is_sha256(value.get("report_fingerprint"))
        or value.get("holdout_sha256") != HYBRID_RESOLVER_PRESSURE_HOLDOUT_SHA256
        or value.get("manifest_sha256") != HYBRID_RESOLVER_PRESSURE_MANIFEST_SHA256
        or any(
            isinstance(counts[field], bool) or not isinstance(counts[field], int)
            for field in counts
        )
        or counts.get("case_count") != HYBRID_RESOLVER_PRESSURE_CASE_COUNT
        or counts.get("raw_relevant_count") != 0
        or counts.get("approved_count") != 0
        or isinstance(upper, bool)
        or not isinstance(upper, (int, float))
        or not math.isfinite(float(upper))
        or not 0.0 <= float(upper) <= 0.05
        or float(upper)
        != one_sided_exact_binomial_upper(0, HYBRID_RESOLVER_PRESSURE_CASE_COUNT)
    ):
        raise ValueError("resolver pressure receipt safety contract mismatch")
    return {
        "artifact": artifact,
        "report_fingerprint": value["report_fingerprint"],
        "holdout_sha256": value["holdout_sha256"],
        "manifest_sha256": value["manifest_sha256"],
        "counts": dict(counts),
        "approved_one_sided_exact_95_upper": float(upper),
    }


def require_resolver_pressure_release_anchor(value: Any) -> dict[str, Any]:
    """Require a structurally valid receipt to match committed release authority."""

    receipt = validate_resolver_pressure_receipt(value)
    if (
        not HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT
        or receipt["report_fingerprint"]
        != HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT
    ):
        raise ValueError("resolver pressure report is not release-allowlisted")
    return receipt


def build_resolver_pressure_receipt(
    report: dict[str, Any], *, artifact: str
) -> dict[str, Any]:
    """Project a fully validated pressure report into content-free evidence."""
    validated = validate_resolver_pressure_report(report)
    if validated["acceptance_gate"]["passed"] is not True:
        raise ValueError("resolver pressure report did not pass its acceptance gate")
    pressure = validated["resolver_pressure"]
    return validate_resolver_pressure_receipt(
        {
            "artifact": artifact,
            "report_fingerprint": validated["report_fingerprint"],
            "holdout_sha256": validated["holdout"]["holdout_sha256"],
            "manifest_sha256": validated["holdout"]["manifest_sha256"],
            "counts": {
                key: pressure["counts"][key]
                for key in (
                    "case_count",
                    "raw_relevant_count",
                    "approved_count",
                )
            },
            "approved_one_sided_exact_95_upper": pressure["metrics"][
                "approved_one_sided_exact_95_upper"
            ],
        }
    )


def load_resolver_pressure_report(
    path: Path,
    *,
    artifact_reference: str | None = None,
) -> dict[str, Any]:
    """Read and validate pressure evidence before the independent holdout."""
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("resolver pressure report path is missing or unsafe")
    raw = candidate.read_bytes()
    if len(raw) > MAX_RESOLVER_PRESSURE_REPORT_BYTES:
        raise ValueError("resolver pressure report is oversized")
    if artifact_reference is None:
        try:
            artifact_reference = (
                candidate.resolve(strict=True)
                .relative_to(ROOT.resolve(strict=True))
                .as_posix()
            )
        except ValueError as exc:
            raise ValueError(
                "resolver pressure report must be inside the repository"
            ) from exc
    report = _parse_json_without_duplicate_keys(raw)
    receipt = build_resolver_pressure_receipt(
        report,
        artifact=_safe_json_artifact_reference(artifact_reference),
    )
    return require_resolver_pressure_release_anchor(receipt)


def _require_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicates")


def _require_safe_source_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_SOURCE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} must be a bounded opaque identifier")
    return value


def _validate_entry_schema(value: Any, field: str) -> None:
    if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
        raise ValueError(f"{field} has an unexpected schema")
    _require_safe_source_id(value["id"], f"{field}.id")
    if not isinstance(value["memory_text"], str) or not value["memory_text"].strip():
        raise ValueError(f"{field}.memory_text must be non-empty")
    if not isinstance(value["metadata"], dict):
        raise TypeError(f"{field}.metadata must be an object")
    _require_string_list(value["domains"], f"{field}.domains")
    _require_string_list(value["stored_files"], f"{field}.stored_files")


def validate_holdout_schema(value: Any) -> dict[str, Any]:
    """Validate the complete frozen public schema and all preregistered counts."""
    if not isinstance(value, dict) or set(value) != _TOP_LEVEL_KEYS:
        raise ValueError("sealed holdout has an unexpected top-level schema")
    backgrounds = value["background_entries"]
    cases = value["cases"]
    if (
        not isinstance(backgrounds, list)
        or len(backgrounds) != EXPECTED_BACKGROUND_COUNT
    ):
        raise ValueError("sealed holdout background count mismatch")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError("sealed holdout case count mismatch")

    entry_ids: set[str] = set()
    for index, entry in enumerate(backgrounds):
        _validate_entry_schema(entry, f"background_entries[{index}]")
        if entry["id"] in entry_ids:
            raise ValueError("sealed holdout contains duplicate entry IDs")
        entry_ids.add(entry["id"])

    case_ids: set[str] = set()
    positive_count = 0
    hard_negative_count = 0
    for index, case in enumerate(cases):
        field = f"cases[{index}]"
        if not isinstance(case, dict) or set(case) != _CASE_KEYS:
            raise ValueError(f"{field} has an unexpected schema")
        _require_safe_source_id(case["id"], f"{field}.id")
        if case["id"] in case_ids:
            raise ValueError("sealed holdout contains duplicate case IDs")
        case_ids.add(case["id"])
        if not isinstance(case["query"], str) or not case["query"].strip():
            raise ValueError(f"{field}.query must be non-empty")
        _require_string_list(case["current_files"], f"{field}.current_files")
        _require_string_list(case["active_domains"], f"{field}.active_domains")
        _validate_entry_schema(case["entry"], f"{field}.entry")
        target_id = case["entry"]["id"]
        if target_id in entry_ids:
            raise ValueError("sealed holdout contains duplicate entry IDs")
        entry_ids.add(target_id)
        if not isinstance(case["expected_relevant"], bool):
            raise TypeError(f"{field}.expected_relevant must be boolean")
        if case["polarity"] not in {"positive", "hard_negative"}:
            raise ValueError(f"{field}.polarity is invalid")
        if not isinstance(case["relation"], str) or not case["relation"].strip():
            raise ValueError(f"{field}.relation must be non-empty")
        expected_positive = case["polarity"] == "positive"
        if case["expected_relevant"] is not expected_positive:
            raise ValueError(f"{field} label and polarity disagree")
        positive_count += int(expected_positive)
        hard_negative_count += int(not expected_positive)

    if positive_count != EXPECTED_POSITIVE_COUNT:
        raise ValueError("sealed holdout positive count mismatch")
    if hard_negative_count != EXPECTED_HARD_NEGATIVE_COUNT:
        raise ValueError("sealed holdout hard-negative count mismatch")
    return value


def load_frozen_holdout(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> FrozenHoldout:
    """Verify bytes, manifest, schema, counts, and entry constructibility."""
    expected = expected_sha256 or EXPECTED_HOLDOUT_SHA256
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("sealed holdout path is missing or unsafe")
    raw_holdout = candidate.read_bytes()
    if len(raw_holdout) > MAX_HOLDOUT_BYTES:
        raise ValueError("sealed holdout is oversized")
    actual = hashlib.sha256(raw_holdout).hexdigest()
    if actual != expected:
        raise ValueError("sealed holdout SHA256 mismatch")

    manifest = candidate.with_name("MANIFEST.sha256")
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("sealed holdout manifest is missing or unsafe")
    raw_manifest = manifest.read_bytes()
    actual_manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    expected_manifest = expected_manifest_sha256 or EXPECTED_MANIFEST_SHA256
    if actual_manifest_sha256 != expected_manifest:
        raise ValueError("sealed holdout manifest SHA256 mismatch")
    try:
        manifest_text = raw_manifest.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("sealed holdout manifest is not UTF-8") from exc
    manifest_rows = [line.split() for line in manifest_text.splitlines() if line]
    matching_rows = [row for row in manifest_rows if row == [expected, candidate.name]]
    if len(matching_rows) != 1:
        raise ValueError("sealed holdout manifest does not bind holdout.json")
    dataset = validate_holdout_schema(_parse_json_without_duplicate_keys(raw_holdout))
    # Authority metadata is validated through the same constructor used by the
    # evaluation, still before any provider is created.
    _build_entries(dataset)
    return FrozenHoldout(dataset, actual, actual_manifest_sha256)


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"entry metadata.{key} must be a string list")
    return list(dict.fromkeys(item for item in value if item.strip()))


def _memory_entry(
    source: dict[str, Any], *, relation: str = "background"
) -> MemoryEntry:
    metadata = dict(source["metadata"])
    stored_files = list(source["stored_files"])
    metadata_files = _metadata_string_list(metadata, "file_paths")
    if stored_files or metadata_files:
        metadata["file_paths"] = list(dict.fromkeys((*metadata_files, *stored_files)))

    scope_raw = str(metadata.get("scope", "project"))
    tier_raw = str(metadata.get("tier", "long_term"))
    try:
        scope = MemoryScope(scope_raw)
        tier = MemoryTier(tier_raw)
    except ValueError as exc:
        raise ValueError(
            "entry authority metadata has an invalid scope or tier"
        ) from exc
    tags = _metadata_string_list(metadata, "tags")
    return MemoryEntry(
        id=source["id"],
        scope=scope,
        category=str(metadata.get("category") or relation),
        content=source["memory_text"],
        tags=tags,
        domains=list(source["domains"]),
        metadata=metadata,
        approval_status=str(metadata.get("approval_status", "approved")),
        lifecycle_status=str(metadata.get("lifecycle_status", "active")),
        safety_status=str(metadata.get("safety_status", "safe")),
        curator_locked=bool(metadata.get("curator_locked", False)),
        tier=tier,
        created_at=REFERENCE_TIME,
        updated_at=REFERENCE_TIME,
        last_accessed=REFERENCE_TIME,
        source="synthetic-independent-holdout",
    )


def _build_entries(
    dataset: dict[str, Any],
) -> tuple[tuple[MemoryEntry, ...], dict[str, MemoryEntry]]:
    backgrounds = tuple(
        _memory_entry(source, relation="background")
        for source in dataset["background_entries"]
    )
    targets = {
        case["id"]: _memory_entry(case["entry"], relation=case["relation"])
        for case in dataset["cases"]
    }
    return backgrounds, targets


def _manager(root: Path, entries: Sequence[MemoryEntry]) -> MemoryManager:
    manager = MemoryManager(project_root=root, data_root=root / "user-memory")
    for entry in entries:
        manager.memories[entry.scope].entries.append(entry)
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


def _encode_documents(encoder: Any, texts: Sequence[str], sink: UsageCollector) -> Any:
    return encoder.encode_documents(
        texts,
        call_context={
            "event_sink": sink,
            "purpose": "memory_hybrid_embedding_global_documents",
        },
    )


def _encode_query(encoder: Any, text: str, sink: UsageCollector) -> Any:
    return encoder.encode_queries(
        [text],
        call_context={
            "event_sink": sink,
            "purpose": "memory_hybrid_embedding_global_query",
        },
    )[0]


def _relation_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _report_identifier(namespace: str, value: str) -> str:
    """Project a sealed identifier to a stable, content-free join key."""

    return hashlib.sha256(
        f"hybrid-report-id-v1\0{namespace}\0{value}".encode()
    ).hexdigest()


def evaluate_global_dense(
    dataset: dict[str, Any],
    *,
    encoder: Any,
    usage_sink: UsageCollector,
) -> dict[str, Any]:
    """Measure Recall@20 against one global eligible corpus."""
    backgrounds, targets = _build_entries(dataset)
    all_entries = (*backgrounds, *targets.values())
    eligible = tuple(entry for entry in all_entries if _entry_is_dense_eligible(entry))
    vectors = _encode_documents(
        encoder,
        [_entry_document(entry) for entry in eligible],
        usage_sink,
    )
    vector_by_id = {
        entry.id: vector for entry, vector in zip(eligible, vectors, strict=True)
    }
    rows: list[dict[str, Any]] = []
    relation_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    positive_hits = 0
    for case in dataset["cases"]:
        request = MemoryRetrievalRequest(
            query=case["query"],
            current_files=tuple(case["current_files"]),
            active_domains=tuple(case["active_domains"]),
        )
        query_vector = _encode_query(encoder, _query_document(request), usage_sink)
        ranked = sorted(
            (
                (
                    entry_id,
                    sum(
                        left * right
                        for left, right in zip(query_vector, vector, strict=True)
                    ),
                )
                for entry_id, vector in vector_by_id.items()
            ),
            key=lambda item: (-item[1], item[0]),
        )
        top_ids = [entry_id for entry_id, _score in ranked[:DEFAULT_DENSE_TOP_K]]
        target_id = targets[case["id"]].id
        target_rank = next(
            (
                index
                for index, (entry_id, _score) in enumerate(ranked, 1)
                if entry_id == target_id
            ),
            None,
        )
        hit = target_id in top_ids
        if case["expected_relevant"]:
            positive_hits += int(hit)
            relation_counts[case["relation"]][0] += int(hit)
            relation_counts[case["relation"]][1] += 1
        rows.append(
            {
                "case_id": _report_identifier("case", case["id"]),
                "target_id": _report_identifier("entry", target_id),
                "polarity": case["polarity"],
                "relation_sha256": _relation_digest(case["relation"]),
                "target_rank": target_rank,
                "target_in_top_20": hit,
                "top_20_ids_sha256": hashlib.sha256(
                    "\n".join(
                        _report_identifier("entry", entry_id) for entry_id in top_ids
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    category_rows = {
        _relation_digest(relation): {
            "hits": values[0],
            "total": values[1],
            "recall_at_20": values[0] / values[1],
        }
        for relation, values in sorted(relation_counts.items())
    }
    category_macro = sum(row["recall_at_20"] for row in category_rows.values()) / max(
        1, len(category_rows)
    )
    return {
        "eligible_corpus_size": len(eligible),
        "positive_hits": positive_hits,
        "positive_total": EXPECTED_POSITIVE_COUNT,
        "candidate_recall_at_20": positive_hits / EXPECTED_POSITIVE_COUNT,
        "category_macro_recall_at_20": category_macro,
        "categories": category_rows,
        "cases": rows,
    }


def _evaluation_evidence(
    identity: dict[str, Any],
    verifier_model: str,
    resolver_pressure_receipt: dict[str, Any],
    one_shot_claim: dict[str, int | str],
) -> dict[str, Any]:
    decision_contract = build_hybrid_decision_contract(verifier_model)
    payload = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot_claim": validate_promotion_one_shot_claim(one_shot_claim),
        "holdout": {
            "version": HOLDOUT_VERSION,
            "file_sha256": EXPECTED_HOLDOUT_SHA256,
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "background_count": EXPECTED_BACKGROUND_COUNT,
            "case_count": EXPECTED_CASE_COUNT,
            "positive_count": EXPECTED_POSITIVE_COUNT,
            "hard_negative_count": EXPECTED_HARD_NEGATIVE_COUNT,
        },
        "promotion_report": {
            "artifact": REPORT_ARTIFACT,
            "report_fingerprint": "0" * 64,
        },
        "implementation_freeze": build_implementation_freeze(),
        "model": dict(identity),
        "embedding_provider": "qwen",
        "verifier_route": qwen_verifier_route_identity(),
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "runtime_implementation_freeze": build_hybrid_runtime_freeze(ROOT),
        "resolver_pressure_receipt": validate_resolver_pressure_receipt(
            resolver_pressure_receipt
        ),
        **decision_contract,
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "max_model_calls_per_task": DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "acceptance_gate": {"passed": True, "evaluation_bootstrap": True},
        "production_enablement_allowed": False,
    }
    payload["report_fingerprint"] = evidence_fingerprint(payload)
    return payload


def validate_qwen_identity(identity: Any) -> dict[str, Any]:
    if not isinstance(identity, dict):
        raise TypeError("Qwen embedding identity is missing")
    dimension = identity.get("dimension")
    fingerprint = identity.get("canary_fingerprint")
    if (
        set(identity) != _MODEL_IDENTITY_KEYS
        or identity.get("provider") != "qwen"
        or identity.get("model_id") != EXPECTED_QWEN_MODEL
        or str(identity.get("endpoint", "")).rstrip("/") != EXPECTED_QWEN_ENDPOINT
        or isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or dimension != QWEN_EMBEDDING_DIMENSION
        or identity.get("representation_version") != MEMORY_REPRESENTATION_VERSION
        or identity.get("canary_version") != REMOTE_EMBEDDING_CANARY_VERSION
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in _HEX_64 for character in fingerprint)
    ):
        raise ValueError(
            "Qwen promotion requires the pinned text-embedding-v3 identity"
        )
    return dict(identity)


def create_exact_subagent_verifier(
    runtime: dict[str, Any],
    verifier_model: str,
    *,
    adapter_factory: Callable[[str, Any, dict[str, Any]], Any],
) -> Any:
    """Construct only a verifier explicitly named by the subagent route."""
    target = str(verifier_model).strip()
    configured = runtime.get("subagentModels")
    configured_models = (
        {str(value).strip() for value in configured.values() if str(value).strip()}
        if isinstance(configured, dict)
        else set()
    )
    provider = str(runtime.get("subagentProvider") or "").strip().lower()
    base_url = str(runtime.get("subagentBaseUrl") or "").strip().rstrip("/")
    if (
        not runtime.get("subagentRoutingEnabled")
        or target not in configured_models
        or provider != QWEN_VERIFIER_PROVIDER
        or base_url != QWEN_VERIFIER_BASE_URL
        or not str(runtime.get("subagentApiKey") or "").strip()
    ):
        raise RuntimeError("verifier_model_is_not_an_exact_configured_subagent_route")
    pinned = build_hybrid_promotion_verifier_runtime(runtime)
    adapter = adapter_factory(target, None, pinned)
    adapter_runtime = getattr(adapter, "runtime", None)
    if (
        not isinstance(adapter_runtime, dict)
        or adapter_runtime.get("model") != target
        or adapter_runtime.get("provider") != "custom"
        or str(adapter_runtime.get("customBaseUrl") or "").rstrip("/")
        != QWEN_VERIFIER_BASE_URL
    ):
        raise RuntimeError("verifier_adapter_model_identity_mismatch")
    return adapter


def _project_hybrid_diagnostics(value: Any) -> dict[str, Any]:
    """Project the provider-owned diagnostic object into the sealed report."""

    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {
        "provider": str(source.get("provider") or "")[:64],
        "cache_hit": bool(source.get("cache_hit", False)),
    }
    for key in _HYBRID_INTEGER_FIELDS:
        raw = source.get(key, 0)
        projected[key] = (
            raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
        )
    reasons = source.get("deterministic_rejection_reasons")
    projected["deterministic_rejection_reason_codes"] = sorted(
        {
            str(reason)[:64]
            for reason in (reasons.values() if isinstance(reasons, dict) else ())
            if reason
        }
    )
    return projected


def _project_canonical_hybrid_diagnostics(value: Any) -> dict[str, Any]:
    """Unwrap canonical retrieval diagnostics before content-free projection.

    ``CanonicalMemoryRetriever`` deliberately owns an outer lifecycle envelope
    (active/fallback/counts) and nests provider-owned evidence below
    ``hybrid.provider``.  Keeping this boundary explicit prevents a synthetic
    flat test fixture from silently diverging from the production result shape.
    A missing provider object remains an all-zero, empty-identity projection;
    the report validator permits that shape only for an explicit fail-closed
    provider fallback row.
    """

    envelope = value if isinstance(value, Mapping) else {}
    provider = envelope.get("provider")
    return _project_hybrid_diagnostics(
        dict(provider) if isinstance(provider, Mapping) else {}
    )


def _hybrid_stage_counts_are_coherent(diagnostics: Mapping[str, Any]) -> bool:
    """Check the content-free accounting identity for one provider result."""

    try:
        candidate_count = diagnostics["candidate_count"]
        dense_candidate_count = diagnostics["dense_candidate_count"]
        dense_lane_count = diagnostics["dense_lane_count"]
        lexical_rescue_count = diagnostics["lexical_rescue_count"]
        adjudicated_count = diagnostics["adjudicated_count"]
        deterministic_accepted_count = diagnostics["deterministic_accepted_count"]
        deterministic_rejected_count = diagnostics["deterministic_rejected_count"]
        query_gate_rejected_count = diagnostics["query_gate_rejected_count"]
        preliminary_accepted_count = diagnostics["preliminary_accepted_count"]
        challenged_count = diagnostics["challenged_count"]
        challenger_accepted_count = diagnostics["challenger_accepted_count"]
        resolver_attempted_count = diagnostics["resolver_attempted_count"]
        resolver_input_rejected_count = diagnostics[
            "resolver_input_rejected_count"
        ]
        resolver_disagreement_count = diagnostics["resolver_disagreement_count"]
        resolver_accepted_count = diagnostics["resolver_accepted_count"]
        semantic_accepted_count = diagnostics["semantic_accepted_count"]
        accepted_count = diagnostics["accepted_count"]
        model_call_count = diagnostics["model_call_count"]
        input_tokens = diagnostics["input_tokens"]
        output_tokens = diagnostics["output_tokens"]
        verifier_model_call_count = diagnostics["verifier_model_call_count"]
        verifier_input_tokens = diagnostics["verifier_input_tokens"]
        verifier_output_tokens = diagnostics["verifier_output_tokens"]
        challenger_model_call_count = diagnostics["challenger_model_call_count"]
        challenger_input_tokens = diagnostics["challenger_input_tokens"]
        challenger_output_tokens = diagnostics["challenger_output_tokens"]
        resolver_model_call_count = diagnostics["resolver_model_call_count"]
        resolver_input_tokens = diagnostics["resolver_input_tokens"]
        resolver_output_tokens = diagnostics["resolver_output_tokens"]
        cache_hit = diagnostics["cache_hit"]
    except KeyError:
        return False

    if not isinstance(cache_hit, bool) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (
            candidate_count,
            dense_candidate_count,
            dense_lane_count,
            lexical_rescue_count,
            adjudicated_count,
            deterministic_accepted_count,
            deterministic_rejected_count,
            query_gate_rejected_count,
            preliminary_accepted_count,
            challenged_count,
            challenger_accepted_count,
            resolver_attempted_count,
            resolver_input_rejected_count,
            resolver_disagreement_count,
            resolver_accepted_count,
            semantic_accepted_count,
            accepted_count,
            model_call_count,
            input_tokens,
            output_tokens,
            verifier_model_call_count,
            verifier_input_tokens,
            verifier_output_tokens,
            challenger_model_call_count,
            challenger_input_tokens,
            challenger_output_tokens,
            resolver_model_call_count,
            resolver_input_tokens,
            resolver_output_tokens,
        )
    ):
        return False
    if (
        candidate_count > MAX_CANDIDATES
        or dense_candidate_count > DEFAULT_DENSE_TOP_K
        or dense_lane_count != dense_candidate_count
        or dense_lane_count + lexical_rescue_count != candidate_count
        or (
            adjudicated_count
            + deterministic_accepted_count
            + deterministic_rejected_count
            + query_gate_rejected_count
            != candidate_count
        )
        or preliminary_accepted_count > adjudicated_count
        or challenged_count != preliminary_accepted_count
        or challenger_accepted_count > challenged_count
        or resolver_attempted_count > challenged_count
        or resolver_attempted_count
        > challenged_count - challenger_accepted_count
        or resolver_input_rejected_count > resolver_attempted_count
        or resolver_attempted_count != resolver_disagreement_count
        or resolver_accepted_count
        > resolver_disagreement_count - resolver_input_rejected_count
        or semantic_accepted_count
        != challenger_accepted_count + resolver_accepted_count
        or semantic_accepted_count > preliminary_accepted_count
        or accepted_count != deterministic_accepted_count + semantic_accepted_count
        or model_call_count
        != (
            verifier_model_call_count
            + challenger_model_call_count
            + resolver_model_call_count
        )
        or input_tokens
        != verifier_input_tokens + challenger_input_tokens + resolver_input_tokens
        or output_tokens
        != verifier_output_tokens
        + challenger_output_tokens
        + resolver_output_tokens
        or model_call_count > DEFAULT_MAX_MODEL_CALLS_PER_TASK
    ):
        return False

    if cache_hit:
        return (
            verifier_model_call_count,
            challenger_model_call_count,
            resolver_model_call_count,
        ) == (0, 0, 0)
    resolver_model_candidates = (
        resolver_attempted_count - resolver_input_rejected_count
    )
    resolver_min_calls = (
        math.ceil(resolver_model_candidates / DEFAULT_BATCH_SIZE)
        if resolver_model_candidates
        else 0
    )
    return bool(
        verifier_model_call_count
        == (
            math.ceil(adjudicated_count / DEFAULT_BATCH_SIZE)
            if adjudicated_count
            else 0
        )
        and challenger_model_call_count
        == (
            math.ceil(preliminary_accepted_count / DEFAULT_BATCH_SIZE)
            if preliminary_accepted_count
            else 0
        )
        and resolver_min_calls
        <= resolver_model_call_count
        <= resolver_model_candidates
    )


def _canonical_hybrid_report_fields(
    value: Any,
    *,
    runtime_fallback: bool = False,
    observed_candidate_count: int | None = None,
    observed_admitted_count: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Return fail-closed status plus the bounded provider projection."""

    envelope = value if isinstance(value, Mapping) else {}
    provider = envelope.get("provider")
    provider = provider if isinstance(provider, Mapping) else {}
    projected = _project_canonical_hybrid_diagnostics(envelope)
    acceptance_reasons = provider.get("deterministic_acceptance_reasons")
    rejection_reasons = provider.get("deterministic_rejection_reasons")
    complete_provider_diagnostics = (
        provider.get("provider") == _EXPECTED_HYBRID_DIAGNOSTIC_PROVIDER
        and isinstance(provider.get("provider_cache_reused"), bool)
        and isinstance(provider.get("cache_hit"), bool)
        and isinstance(acceptance_reasons, Mapping)
        and isinstance(rejection_reasons, Mapping)
        and all(
            isinstance(provider.get(field), int)
            and not isinstance(provider.get(field), bool)
            and provider.get(field) >= 0
            for field in _HYBRID_INTEGER_FIELDS
        )
        and len(acceptance_reasons) == provider.get("deterministic_accepted_count")
        and len(rejection_reasons) == provider.get("deterministic_rejected_count")
        and all(
            isinstance(entry_id, str) and bool(entry_id) and reason == "proven_rename"
            for entry_id, reason in acceptance_reasons.items()
        )
        and all(
            isinstance(entry_id, str)
            and bool(entry_id)
            and isinstance(reason, str)
            and reason in _DETERMINISTIC_REASON_CODES - {"proven_rename"}
            for entry_id, reason in rejection_reasons.items()
        )
        and _hybrid_stage_counts_are_coherent(projected)
    )
    if observed_candidate_count is not None:
        complete_provider_diagnostics = bool(
            complete_provider_diagnostics
            and projected["candidate_count"] == observed_candidate_count
            and envelope.get("candidate_count") == observed_candidate_count
        )
    if observed_admitted_count is not None:
        complete_provider_diagnostics = bool(
            complete_provider_diagnostics
            and projected["accepted_count"] == observed_admitted_count
            and envelope.get("accepted_count") == observed_admitted_count
        )
    fallback = bool(
        runtime_fallback
        or envelope.get("active") is not True
        or envelope.get("fallback")
        or not complete_provider_diagnostics
    )
    return fallback, projected


def evaluate_canonical(
    dataset: dict[str, Any],
    *,
    model: Any,
    client: Any,
    identity: dict[str, Any],
    verifier_model: str,
    resolver_pressure_receipt: dict[str, Any],
    one_shot_claim: dict[str, int | str],
    workspace: Path,
    usage_sink: UsageCollector,
) -> dict[str, Any]:
    """Run complete case-local canonical retrieval with global shared backgrounds."""
    backgrounds, targets = _build_entries(dataset)
    manager = _manager(workspace, backgrounds)

    def provider_factory(**kwargs: Any) -> Any:
        return create_hybrid_candidate_provider(
            **kwargs,
            embedding_client_factory=lambda _workspace: client,
        )

    pipeline = MemoryPipeline(manager, hybrid_provider_factory=provider_factory)
    evaluation_evidence = _evaluation_evidence(
        identity,
        verifier_model,
        resolver_pressure_receipt,
        one_shot_claim,
    )
    evidence_path = workspace / "evaluation-evidence.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evaluation_evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with patch(
        "minicode.memory_hybrid.HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT",
        evaluation_evidence["report_fingerprint"],
    ):
        pipeline.initialize(
            model_adapter=model,
            workspace_path=str(workspace),
            enable_vector=True,
            hybrid_evidence_path=evidence_path,
            hybrid_embedding_provider="qwen",
            allow_remote_memory_embedding=True,
            hybrid_verifier_binding="evaluation_exact_subagent",
            _hybrid_evaluation_bootstrap=True,
        )
    if not pipeline.stats["hybrid_active"]:
        inactive_reason = pipeline.stats["hybrid_inactive_reason"]
        raise RuntimeError(f"canonical hybrid did not activate: {inactive_reason}")

    forbidden_entries = {
        entry.id: entry
        for entry in (*backgrounds, *targets.values())
        if not _entry_is_dense_eligible(entry)
    }
    lifecycle_forbidden = {
        entry_id
        for entry_id, entry in forbidden_entries.items()
        if entry.lifecycle_status != "active"
    }
    safety_forbidden = {
        entry_id
        for entry_id, entry in forbidden_entries.items()
        if entry.safety_status != "safe"
        or entry.approval_status != "approved"
        or entry.curator_locked
    }

    rows: list[dict[str, Any]] = []
    candidate_positive_hits = 0
    admitted_positive_hits = 0
    rendered_positive_hits = 0
    false_positive_admissions = 0
    false_positive_renders = 0
    hard_negative_target_admissions = 0
    hard_negative_target_renders = 0
    lifecycle_leakage_count = 0
    safety_leakage_count = 0
    forbidden_leakage_count = 0
    provider_fallback_count = 0
    id_mismatch_count = 0
    forbidden_indexed_ids: set[str] = set()

    for case in dataset["cases"]:
        target = targets[case["id"]]
        case_entries = (*backgrounds, target)
        case_entry_ids = {entry.id for entry in case_entries}
        for scope, memory_file in manager.memories.items():
            memory_file.entries[:] = [
                entry for entry in case_entries if entry.scope == scope
            ]
            memory_file._rebuild_indices()
        pipeline.read(
            case["query"],
            current_files=list(case["current_files"]),
            active_domains=list(case["active_domains"]),
            max_results=MAX_CANDIDATES,
            max_total_tokens=12_000,
            max_tokens_per_memory=400,
            context_usage=0.0,
            _record_retrieval=False,
            event_sink=usage_sink,
        )
        result = pipeline._last_retrieval_result
        candidate_ids = tuple(result.candidate_ids)
        admitted_ids = tuple(result.admitted_ids)
        rendered_ids = tuple(result.rendered_ids)
        foreign_ids = (
            set(candidate_ids) | set(admitted_ids) | set(rendered_ids)
        ) - case_entry_ids
        id_mismatch_count += len(foreign_ids)
        target_candidate = target.id in candidate_ids
        target_admitted = target.id in admitted_ids
        target_rendered = target.id in rendered_ids
        if case["expected_relevant"]:
            candidate_positive_hits += int(target_candidate)
            admitted_positive_hits += int(target_admitted)
            rendered_positive_hits += int(target_rendered)
            false_positive_admissions += sum(
                entry_id != target.id for entry_id in admitted_ids
            )
            false_positive_renders += sum(
                entry_id != target.id for entry_id in rendered_ids
            )
        else:
            hard_negative_target_admissions += int(target_admitted)
            false_positive_admissions += len(admitted_ids)
            hard_negative_target_renders += int(target_rendered)
            false_positive_renders += len(rendered_ids)

        rendered_set = set(rendered_ids)
        lifecycle_leakage_ids = sorted(rendered_set & lifecycle_forbidden)
        safety_leakage_ids = sorted(rendered_set & safety_forbidden)
        forbidden_leakage_ids = sorted(rendered_set & set(forbidden_entries))
        lifecycle_leakage_count += len(lifecycle_leakage_ids)
        safety_leakage_count += len(safety_leakage_ids)
        forbidden_leakage_count += len(forbidden_leakage_ids)
        provider = pipeline._retriever._hybrid_provider
        forbidden_indexed_ids.update(
            set(getattr(provider, "_records", {})) & set(forbidden_entries)
        )
        hybrid_runtime = result.diagnostics.get("hybrid_runtime", {})
        hybrid_diagnostics = result.diagnostics.get("hybrid", {})
        fallback, projected_hybrid_diagnostics = _canonical_hybrid_report_fields(
            hybrid_diagnostics,
            runtime_fallback=bool(hybrid_runtime.get("fallback")),
            observed_candidate_count=len(candidate_ids),
            observed_admitted_count=len(admitted_ids),
        )
        provider_fallback_count += int(fallback)
        target_memory = next(
            (item for item in result.candidates if item.entry_id == target.id),
            None,
        )
        rows.append(
            {
                "case_id": _report_identifier("case", case["id"]),
                "target_id": _report_identifier("entry", target.id),
                "polarity": case["polarity"],
                "relation_sha256": _relation_digest(case["relation"]),
                "candidate_ids": [
                    _report_identifier("entry", entry_id) for entry_id in candidate_ids
                ],
                "admitted_ids": [
                    _report_identifier("entry", entry_id) for entry_id in admitted_ids
                ],
                "rendered_ids": [
                    _report_identifier("entry", entry_id) for entry_id in rendered_ids
                ],
                "target_candidate": target_candidate,
                "target_admitted": target_admitted,
                "target_rendered": target_rendered,
                "target_reason_codes": (
                    list(target_memory.reason_codes)
                    if target_memory is not None
                    else []
                ),
                "foreign_ids": sorted(
                    _report_identifier("entry", entry_id) for entry_id in foreign_ids
                ),
                "lifecycle_leakage_ids": [
                    _report_identifier("entry", entry_id)
                    for entry_id in lifecycle_leakage_ids
                ],
                "safety_leakage_ids": [
                    _report_identifier("entry", entry_id)
                    for entry_id in safety_leakage_ids
                ],
                "forbidden_leakage_ids": [
                    _report_identifier("entry", entry_id)
                    for entry_id in forbidden_leakage_ids
                ],
                "provider_fallback": fallback,
                "hybrid": projected_hybrid_diagnostics,
            }
        )

    admitted_precision = admitted_positive_hits / max(
        1, admitted_positive_hits + false_positive_admissions
    )
    rendered_precision = rendered_positive_hits / max(
        1, rendered_positive_hits + false_positive_renders
    )
    hard_negative_upper = one_sided_exact_binomial_upper(
        hard_negative_target_renders,
        EXPECTED_HARD_NEGATIVE_COUNT,
    )
    hard_negative_admission_upper = one_sided_exact_binomial_upper(
        hard_negative_target_admissions,
        EXPECTED_HARD_NEGATIVE_COUNT,
    )
    return {
        "counts": {
            "positive_total": EXPECTED_POSITIVE_COUNT,
            "hard_negative_total": EXPECTED_HARD_NEGATIVE_COUNT,
            "candidate_positive_hits": candidate_positive_hits,
            "admitted_positive_hits": admitted_positive_hits,
            "rendered_positive_hits": rendered_positive_hits,
            "false_positive_admissions": false_positive_admissions,
            "false_positive_renders": false_positive_renders,
            "hard_negative_target_admissions": hard_negative_target_admissions,
            "hard_negative_target_renders": hard_negative_target_renders,
            "lifecycle_leakage_count": lifecycle_leakage_count,
            "safety_leakage_count": safety_leakage_count,
            "forbidden_leakage_count": forbidden_leakage_count,
            "forbidden_indexed_count": len(forbidden_indexed_ids),
            "provider_fallback_count": provider_fallback_count,
            "id_mismatch_count": id_mismatch_count,
        },
        "metrics": {
            "canonical_candidate_positive_recall": candidate_positive_hits
            / EXPECTED_POSITIVE_COUNT,
            "admitted_positive_recall": admitted_positive_hits
            / EXPECTED_POSITIVE_COUNT,
            "admitted_precision": admitted_precision,
            "rendered_positive_recall": rendered_positive_hits
            / EXPECTED_POSITIVE_COUNT,
            "rendered_precision": rendered_precision,
            "hard_negative_target_admission_rate": hard_negative_target_admissions
            / EXPECTED_HARD_NEGATIVE_COUNT,
            "hard_negative_admission_one_sided_exact_95_upper": (
                hard_negative_admission_upper
            ),
            "hard_negative_target_render_rate": hard_negative_target_renders
            / EXPECTED_HARD_NEGATIVE_COUNT,
            "hard_negative_one_sided_exact_95_upper": hard_negative_upper,
        },
        "forbidden_indexed_ids": sorted(
            _report_identifier("entry", entry_id) for entry_id in forbidden_indexed_ids
        ),
        "cases": rows,
    }


def one_sided_exact_binomial_upper(
    successes: int,
    trials: int,
    *,
    alpha: float = 0.05,
) -> float:
    """Clopper-Pearson one-sided upper confidence bound without SciPy."""
    if (
        isinstance(successes, bool)
        or isinstance(trials, bool)
        or not isinstance(successes, int)
        or not isinstance(trials, int)
        or trials <= 0
        or not 0 <= successes <= trials
        or not 0.0 < alpha < 1.0
    ):
        raise ValueError("invalid exact-binomial inputs")
    if successes == trials:
        return 1.0
    if successes == 0:
        return 1.0 - alpha ** (1.0 / trials)

    def cdf(probability: float) -> float:
        return sum(
            math.comb(trials, index)
            * probability**index
            * (1.0 - probability) ** (trials - index)
            for index in range(successes + 1)
        )

    low = successes / trials
    high = 1.0
    for _ in range(100):
        middle = (low + high) / 2.0
        if cdf(middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def acceptance_checks(report: dict[str, Any]) -> dict[str, bool]:
    dense = report["global_dense"]
    canonical = report["canonical"]
    metrics = canonical["metrics"]
    counts = canonical["counts"]
    holdout = report["holdout"]
    thresholds = report["thresholds"]
    try:
        validate_promotion_one_shot_claim(report.get("one_shot_claim"))
    except (TypeError, ValueError):
        one_shot_claim_bound = False
    else:
        one_shot_claim_bound = True
    return {
        "one_shot_claim_bound": one_shot_claim_bound,
        "implementation_freeze_unchanged": report.get("implementation_freeze")
        == build_implementation_freeze(),
        "runtime_implementation_freeze_unchanged": report.get(
            "runtime_implementation_freeze"
        )
        == build_hybrid_runtime_freeze(ROOT),
        "frozen_holdout": (
            holdout
            == {
                "version": HOLDOUT_VERSION,
                "file_sha256": EXPECTED_HOLDOUT_SHA256,
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "background_count": EXPECTED_BACKGROUND_COUNT,
                "case_count": EXPECTED_CASE_COUNT,
                "positive_count": EXPECTED_POSITIVE_COUNT,
                "hard_negative_count": EXPECTED_HARD_NEGATIVE_COUNT,
            }
            and _is_sha256(holdout.get("manifest_sha256"))
        ),
        "candidate_recall_at_20": dense["candidate_recall_at_20"]
        >= thresholds["candidate_recall_at_20_min"],
        "category_macro_recall_at_20": dense["category_macro_recall_at_20"]
        >= thresholds["category_macro_recall_at_20_min"],
        "canonical_candidate_positive_recall": metrics[
            "canonical_candidate_positive_recall"
        ]
        >= thresholds["canonical_candidate_positive_recall_min"],
        "admitted_precision": metrics["admitted_precision"]
        >= thresholds["admitted_precision_min"],
        "rendered_positive_recall": metrics["rendered_positive_recall"]
        >= thresholds["rendered_positive_recall_min"],
        "rendered_precision": metrics["rendered_precision"]
        >= thresholds["rendered_precision_min"],
        "hard_negative_target_admission_count": counts[
            "hard_negative_target_admissions"
        ]
        <= thresholds["hard_negative_target_admission_count_max"],
        "hard_negative_admission_one_sided_exact_95_upper": metrics[
            "hard_negative_admission_one_sided_exact_95_upper"
        ]
        <= thresholds["hard_negative_admission_one_sided_exact_95_upper_max"],
        "hard_negative_target_render_count": counts["hard_negative_target_renders"]
        <= thresholds["hard_negative_target_render_count_max"],
        "hard_negative_one_sided_exact_95_upper": metrics[
            "hard_negative_one_sided_exact_95_upper"
        ]
        <= thresholds["hard_negative_one_sided_exact_95_upper_max"],
        "lifecycle_leakage": counts["lifecycle_leakage_count"]
        <= thresholds["lifecycle_leakage_count_max"],
        "safety_leakage": counts["safety_leakage_count"]
        <= thresholds["safety_leakage_count_max"],
        "forbidden_leakage": counts["forbidden_leakage_count"]
        <= thresholds["forbidden_leakage_count_max"],
        "forbidden_indexed": counts["forbidden_indexed_count"]
        <= thresholds["forbidden_indexed_count_max"],
        "provider_fallback": counts["provider_fallback_count"]
        <= thresholds["provider_fallback_count_max"],
        "id_mismatch": counts["id_mismatch_count"]
        <= thresholds["id_mismatch_count_max"],
    }


def build_report(
    *,
    frozen: FrozenHoldout,
    implementation_freeze: dict[str, Any],
    runtime_implementation_freeze: dict[str, Any],
    resolver_pressure_receipt: dict[str, Any],
    one_shot_claim: dict[str, int | str],
    identity: dict[str, Any],
    verifier_model: str,
    global_dense: dict[str, Any],
    canonical: dict[str, Any],
    usage: dict[str, Any],
) -> dict[str, Any]:
    if verifier_model != DEFAULT_VERIFIER_MODEL:
        raise ValueError("promotion verifier model is not the preregistered route")
    pressure_receipt = validate_resolver_pressure_receipt(resolver_pressure_receipt)
    validated_claim = validate_promotion_one_shot_claim(one_shot_claim)
    evaluation = _evaluation_evidence(
        identity,
        verifier_model,
        pressure_receipt,
        validated_claim,
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "one_shot_claim": validated_claim,
        "holdout": frozen.summary,
        "implementation_freeze": implementation_freeze,
        "runtime_implementation_freeze": runtime_implementation_freeze,
        "resolver_pressure_receipt": pressure_receipt,
        "embedding_provider": "qwen",
        "remote_memory_authorized": True,
        "model": dict(identity),
        "verifier_route": qwen_verifier_route_identity(),
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "verifier": evaluation["verifier"],
        "challenger": evaluation["challenger"],
        "resolver": evaluation["resolver"],
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "max_model_calls_per_task": DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        "candidate_corpus_protocol": "global_all_eligible_164_max_entries",
        "canonical_corpus_protocol": "case_target_plus_80_shared_background",
        "thresholds": dict(PROMOTION_THRESHOLDS),
        "global_dense": global_dense,
        "canonical": canonical,
        "usage": usage,
    }
    checks = acceptance_checks(report)
    report["acceptance_gate"] = {"passed": all(checks.values()), "checks": checks}
    report["report_fingerprint"] = evidence_fingerprint(report)
    return report


def _require_exact_number(value: Any, expected: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ValueError(f"promotion report {name} mismatch")


def _require_count(value: Any, name: str, *, maximum: int | None = None) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(f"promotion report {name} is invalid")
    return value


def _require_rate(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"promotion report {name} is invalid")
    return float(value)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_64 for character in value)
    )


def _contains_sealed_content_key(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in {"query", "memory_text", "content", "metadata"} for key in value):
            return True
        return any(_contains_sealed_content_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_sealed_content_key(item) for item in value)
    return False


def _validate_usage_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "embedding",
        "embedding_stages",
        "verifier",
        "challenger",
        "resolver",
        "other",
    }:
        raise ValueError("promotion report usage evidence is incomplete")
    expected_fields = {
        "completed_call_count",
        "failed_call_count",
        "input_tokens",
        "output_tokens",
        "usage_unavailable_count",
    }
    validated: dict[str, Any] = {}
    for lane in ("embedding", "verifier", "challenger", "resolver", "other"):
        row = value[lane]
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ValueError(f"promotion report {lane} usage evidence is malformed")
        validated[lane] = {
            field: _require_count(row[field], f"{lane} {field}")
            for field in expected_fields
        }
    stages = value["embedding_stages"]
    if (
        not isinstance(stages, dict)
        or set(stages) != set(UsageCollector._EMBEDDING_STAGES)
    ):
        raise ValueError("promotion report embedding-stage usage is malformed")
    validated_stages: dict[str, dict[str, int]] = {}
    for stage in UsageCollector._EMBEDDING_STAGES:
        row = stages[stage]
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ValueError(
                f"promotion report embedding {stage} usage is malformed"
            )
        validated_stages[stage] = {
            field: _require_count(row[field], f"embedding {stage} {field}")
            for field in expected_fields
        }
    for field in expected_fields:
        if validated["embedding"][field] != sum(
            row[field] for row in validated_stages.values()
        ):
            raise ValueError(
                "promotion report embedding usage disagrees with stage rows"
            )
    validated["embedding_stages"] = validated_stages
    return validated


def _validate_usage_evidence(
    value: Any,
    *,
    canonical: Mapping[str, Any],
    allow_incomplete_model_lanes: bool = False,
) -> None:
    value = _validate_usage_shape(value)
    for required_lane in ("embedding",):
        if value[required_lane]["completed_call_count"] <= 0:
            raise ValueError(f"promotion report {required_lane} usage is absent")
    if not allow_incomplete_model_lanes:
        for required_lane in ("verifier", "challenger"):
            if value[required_lane]["completed_call_count"] <= 0:
                raise ValueError(f"promotion report {required_lane} usage is absent")

        cases = canonical.get("cases")
        if not isinstance(cases, list):
            raise ValueError("promotion report canonical usage join is unavailable")
        expected = {
            lane: {
                "completed_call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
            for lane in ("verifier", "challenger", "resolver")
        }
        for case in cases:
            if not isinstance(case, Mapping) or case.get("provider_fallback") is True:
                raise ValueError("passing usage cannot contain a provider fallback")
            diagnostics = case.get("hybrid")
            if not isinstance(diagnostics, Mapping):
                raise ValueError("promotion report hybrid usage join is unavailable")
            for lane in expected:
                expected[lane]["completed_call_count"] += diagnostics[
                    f"{lane}_model_call_count"
                ]
                expected[lane]["input_tokens"] += diagnostics[
                    f"{lane}_input_tokens"
                ]
                expected[lane]["output_tokens"] += diagnostics[
                    f"{lane}_output_tokens"
                ]

        # Protocol v8.1 (2026-10-22, owner-approved amendment): the verifier
        # lane tolerates retried-then-recovered schema failures. A failed
        # attempt is already self-healed when every case retains a final
        # verdict (the row join above reconciles completed calls against the
        # per-case verdict rows); promotion keys on outcome completeness plus
        # a bounded failure rate instead of a zero-failure attempt history
        # that the verifier model's ~2.5% base schema-violation rate made
        # unwinnable (v9/v10/v11 immutable failure reports).
        for lane, lane_expected in expected.items():
            observed = value[lane]
            if any(observed[field] != count for field, count in lane_expected.items()):
                raise ValueError(
                    f"promotion report {lane} usage disagrees with canonical rows"
                )
            if observed["usage_unavailable_count"] != 0:
                raise ValueError(f"promotion report {lane} usage is incomplete")
            if observed["failed_call_count"] == 0:
                continue
            if lane != "verifier":
                raise ValueError(
                    f"promotion report {lane} usage is incomplete or failed"
                )
            attempts = (
                observed["completed_call_count"] + observed["failed_call_count"]
            )
            tolerance = math.ceil(
                attempts * QWEN_PROMOTION_VERIFIER_FAILED_CALL_RATE
            )
            if observed["failed_call_count"] > tolerance:
                raise ValueError(
                    "promotion report verifier failed-call rate exceeds the "
                    f"preregistered tolerance ({observed['failed_call_count']}"
                    f" failed > {tolerance} allowed of {attempts} attempts)"
                )

        other = value["other"]
        if any(other[field] != 0 for field in other):
            raise ValueError("promotion report contains unattributed model usage")
        embedding = value["embedding"]
        if (
            embedding["failed_call_count"] != 0
            or embedding["usage_unavailable_count"] != 0
        ):
            raise ValueError("promotion report embedding usage is incomplete or failed")
        embedding_stages = value["embedding_stages"]
        # The global document corpus is embedded through the production
        # client, whose hard batch limit is _EMBED_BATCH_LIMIT texts per
        # call; expecting a single call for the whole sealed corpus is
        # unsatisfiable by construction (failed post-claim on the first
        # v9 attempt with zero failed embeddings and exact per-case counts).
        from minicode.embeddings import _EMBED_BATCH_LIMIT
        import math as _math
        expected_embedding_calls = {
            "identity": 2,
            "global_documents": _math.ceil(
                EXPECTED_CASE_COUNT / _EMBED_BATCH_LIMIT
            ),
            "global_query": EXPECTED_CASE_COUNT,
            "canonical_query": EXPECTED_CASE_COUNT,
            "unattributed": 0,
        }
        if any(
            embedding_stages[stage]["completed_call_count"] != count
            for stage, count in expected_embedding_calls.items()
        ):
            raise ValueError(
                "promotion report embedding stage calls disagree with protocol"
            )
        canonical_document_calls = embedding_stages["canonical_documents"][
            "completed_call_count"
        ]
        if not 1 <= canonical_document_calls <= EXPECTED_CASE_COUNT:
            raise ValueError(
                "promotion report canonical document embedding calls are invalid"
            )
        for stage in UsageCollector._EMBEDDING_STAGES:
            stage_usage = embedding_stages[stage]
            if (
                stage_usage["failed_call_count"] != 0
                or stage_usage["usage_unavailable_count"] != 0
            ):
                raise ValueError(
                    "promotion report embedding stage usage is incomplete or failed"
                )


def _classify_post_claim_failure(error: BaseException, *, stage: str) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, (TimeoutError,)) or "timeout" in type(error).__name__.casefold():
        return "timeout"
    if isinstance(error, MemoryError):
        return "resource_error"
    if isinstance(error, (TypeError, ValueError, AssertionError)):
        return "post_claim_validation_error"
    if stage in {"embedding_identity", "global_dense", "canonical_retrieval"}:
        return "provider_or_transport_error"
    return "unexpected_error"


def build_post_claim_failure_report(
    *,
    frozen: FrozenHoldout,
    implementation_freeze: dict[str, Any],
    runtime_implementation_freeze: dict[str, Any],
    resolver_pressure_receipt: dict[str, Any],
    one_shot_claim: dict[str, Any],
    failure_stage: str,
    failure_kind: str,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """Build the only publishable artifact for a consumed failed attempt."""

    payload = {
        "schema_version": POST_CLAIM_FAILURE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "production_authority": False,
        "one_shot_claim": validate_promotion_one_shot_claim(one_shot_claim),
        "holdout": frozen.summary,
        "implementation_freeze": implementation_freeze,
        "runtime_implementation_freeze": runtime_implementation_freeze,
        "resolver_pressure_receipt": validate_resolver_pressure_receipt(
            resolver_pressure_receipt
        ),
        "embedding_provider": "qwen",
        "verifier_route": qwen_verifier_route_identity(),
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "failure_stage": failure_stage,
        "failure_kind": failure_kind,
        "usage": usage,
    }
    payload["report_fingerprint"] = evidence_fingerprint(payload)
    return validate_post_claim_failure_report(payload)


def validate_post_claim_failure_report(report: Any) -> dict[str, Any]:
    """Validate an immutable, content-free record of a spent failed run."""

    if (
        not isinstance(report, dict)
        or set(report) != _POST_CLAIM_FAILURE_REPORT_KEYS
    ):
        raise ValueError("post-claim failure report schema mismatch")
    if _contains_sealed_content_key(report):
        raise ValueError("post-claim failure report leaks sealed content")
    fingerprint = report.get("report_fingerprint")
    if not _is_sha256(fingerprint) or fingerprint != evidence_fingerprint(report):
        raise ValueError("post-claim failure report fingerprint mismatch")
    if (
        report.get("schema_version") != POST_CLAIM_FAILURE_SCHEMA_VERSION
        or report.get("protocol_version") != HYBRID_PROTOCOL_VERSION
        or report.get("synthetic_data") is not True
        or report.get("one_shot") is not True
        or report.get("production_authority") is not False
        or report.get("embedding_provider") != "qwen"
        or report.get("verifier_route") != qwen_verifier_route_identity()
        or report.get("inference_profile")
        != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
        or report.get("failure_stage") not in _POST_CLAIM_FAILURE_STAGES
        or report.get("failure_kind") not in _POST_CLAIM_FAILURE_KINDS
    ):
        raise ValueError("post-claim failure report contract mismatch")
    validate_promotion_one_shot_claim(report.get("one_shot_claim"))
    expected_holdout = {
        "version": HOLDOUT_VERSION,
        "file_sha256": EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "background_count": EXPECTED_BACKGROUND_COUNT,
        "case_count": EXPECTED_CASE_COUNT,
        "positive_count": EXPECTED_POSITIVE_COUNT,
        "hard_negative_count": EXPECTED_HARD_NEGATIVE_COUNT,
    }
    if report.get("holdout") != expected_holdout:
        raise ValueError("post-claim failure report holdout identity mismatch")
    if report.get("implementation_freeze") != build_implementation_freeze():
        raise ValueError("post-claim failure implementation freeze mismatch")
    if report.get("runtime_implementation_freeze") != build_hybrid_runtime_freeze(
        ROOT
    ):
        raise ValueError("post-claim failure runtime freeze mismatch")
    validate_resolver_pressure_receipt(report.get("resolver_pressure_receipt"))
    _validate_usage_shape(report.get("usage"))
    return report


def _validate_dense_evidence(dense: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(dense, dict) or set(dense) != {
        "eligible_corpus_size",
        "positive_hits",
        "positive_total",
        "candidate_recall_at_20",
        "category_macro_recall_at_20",
        "categories",
        "cases",
    }:
        raise ValueError("promotion report global-dense evidence is malformed")
    _require_count(
        dense["eligible_corpus_size"],
        "eligible corpus size",
        maximum=EXPECTED_BACKGROUND_COUNT + EXPECTED_CASE_COUNT,
    )
    if dense["eligible_corpus_size"] <= 0:
        raise ValueError("promotion report eligible corpus is empty")
    _require_exact_number(
        dense["positive_total"], EXPECTED_POSITIVE_COUNT, "dense positive denominator"
    )
    positive_hits = _require_count(
        dense["positive_hits"], "dense positive hits", maximum=EXPECTED_POSITIVE_COUNT
    )
    recall = _require_rate(dense["candidate_recall_at_20"], "dense recall")
    if recall != positive_hits / EXPECTED_POSITIVE_COUNT:
        raise ValueError("promotion report dense recall does not match raw rows")

    rows = dense["cases"]
    if not isinstance(rows, list) or len(rows) != EXPECTED_CASE_COUNT:
        raise ValueError("promotion report global-dense rows are incomplete")
    by_id: dict[str, dict[str, Any]] = {}
    positive_rows = 0
    hard_negative_rows = 0
    row_positive_hits = 0
    category_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    target_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _DENSE_ROW_KEYS:
            raise ValueError("promotion report global-dense row schema mismatch")
        case_id = row["case_id"]
        target_id = row["target_id"]
        if (
            not isinstance(case_id, str)
            or _SAFE_REPORT_ID.fullmatch(case_id) is None
            or case_id in by_id
            or not isinstance(target_id, str)
            or _SAFE_REPORT_ID.fullmatch(target_id) is None
            or target_id in target_ids
        ):
            raise ValueError("promotion report dense IDs are invalid")
        target_ids.add(target_id)
        polarity = row["polarity"]
        if polarity not in {"positive", "hard_negative"}:
            raise ValueError("promotion report dense polarity is invalid")
        positive = polarity == "positive"
        positive_rows += int(positive)
        hard_negative_rows += int(not positive)
        if not _is_sha256(row["relation_sha256"]) or not _is_sha256(
            row["top_20_ids_sha256"]
        ):
            raise ValueError("promotion report dense row hash is invalid")
        hit = row["target_in_top_20"]
        rank = row["target_rank"]
        if not isinstance(hit, bool) or (
            rank is not None
            and (isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0)
        ):
            raise ValueError("promotion report dense rank evidence is invalid")
        if hit != (isinstance(rank, int) and not isinstance(rank, bool) and rank <= 20):
            raise ValueError("promotion report dense hit disagrees with target rank")
        if positive:
            row_positive_hits += int(hit)
            category_counts[row["relation_sha256"]][0] += int(hit)
            category_counts[row["relation_sha256"]][1] += 1
        by_id[case_id] = row
    if (
        positive_rows != EXPECTED_POSITIVE_COUNT
        or hard_negative_rows != EXPECTED_HARD_NEGATIVE_COUNT
        or row_positive_hits != positive_hits
    ):
        raise ValueError("promotion report dense row counts disagree")

    categories = dense["categories"]
    if not isinstance(categories, dict) or not categories:
        raise ValueError("promotion report category evidence is missing")
    expected_categories = {
        relation_hash: {
            "hits": values[0],
            "total": values[1],
            "recall_at_20": values[0] / values[1],
        }
        for relation_hash, values in category_counts.items()
    }
    if categories != expected_categories:
        raise ValueError("promotion report category rows disagree with case rows")
    expected_macro = sum(
        row["recall_at_20"] for row in expected_categories.values()
    ) / len(expected_categories)
    if (
        _require_rate(dense["category_macro_recall_at_20"], "category macro recall")
        != expected_macro
    ):
        raise ValueError("promotion report category macro recall disagrees")
    return by_id


def _require_unique_id_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or _SAFE_REPORT_ID.fullmatch(item) is None
        for item in value
    ):
        raise ValueError(f"promotion report {name} IDs are invalid")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError(f"promotion report {name} IDs contain duplicates")
    return result


def _validate_canonical_evidence(
    canonical: Any,
    dense_by_id: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(canonical, dict) or set(canonical) != {
        "counts",
        "metrics",
        "forbidden_indexed_ids",
        "cases",
    }:
        raise ValueError("promotion report canonical evidence is malformed")
    rows = canonical["cases"]
    if not isinstance(rows, list) or len(rows) != EXPECTED_CASE_COUNT:
        raise ValueError("promotion report canonical rows are incomplete")

    candidate_hits = admitted_hits = rendered_hits = 0
    false_positive_admissions = false_positive_renders = 0
    hard_negative_target_admissions = hard_negative_target_renders = 0
    lifecycle_leakages = safety_leakages = forbidden_leakages = 0
    provider_fallbacks = id_mismatches = 0
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _CANONICAL_ROW_KEYS:
            raise ValueError("promotion report canonical row schema mismatch")
        if not isinstance(row["provider_fallback"], bool):
            raise TypeError("promotion report provider fallback flag is invalid")
        case_id = row["case_id"]
        if (
            not isinstance(case_id, str)
            or case_id in seen
            or case_id not in dense_by_id
        ):
            raise ValueError("promotion report canonical case ID is invalid")
        seen.add(case_id)
        dense_row = dense_by_id[case_id]
        if any(
            row[field] != dense_row[field]
            for field in ("target_id", "polarity", "relation_sha256")
        ):
            raise ValueError("promotion report dense/canonical join mismatch")
        candidates = _require_unique_id_list(row["candidate_ids"], "candidate")
        admitted = _require_unique_id_list(row["admitted_ids"], "admitted")
        rendered = _require_unique_id_list(row["rendered_ids"], "rendered")
        foreign = _require_unique_id_list(row["foreign_ids"], "foreign")
        lifecycle_leakage = _require_unique_id_list(
            row["lifecycle_leakage_ids"], "lifecycle-leakage"
        )
        safety_leakage = _require_unique_id_list(
            row["safety_leakage_ids"], "safety-leakage"
        )
        forbidden_leakage = _require_unique_id_list(
            row["forbidden_leakage_ids"], "forbidden-leakage"
        )
        candidate_limit = (
            EXPECTED_BACKGROUND_COUNT + 1
            if row["provider_fallback"]
            else MAX_CANDIDATES
        )
        if (
            len(candidates) > candidate_limit
            or not set(admitted) <= set(candidates)
            or not set(rendered) <= set(admitted)
            or not set(lifecycle_leakage) <= set(rendered)
            or not set(safety_leakage) <= set(rendered)
            or not set(forbidden_leakage) <= set(rendered)
            or not set(lifecycle_leakage) <= set(forbidden_leakage)
            or not set(safety_leakage) <= set(forbidden_leakage)
        ):
            raise ValueError("promotion report canonical stage membership is invalid")
        for field, membership in (
            ("target_candidate", row["target_id"] in candidates),
            ("target_admitted", row["target_id"] in admitted),
            ("target_rendered", row["target_id"] in rendered),
        ):
            if not isinstance(row[field], bool) or row[field] is not membership:
                raise ValueError("promotion report target stage flag is inconsistent")
        reason_codes = row["target_reason_codes"]
        if (
            not isinstance(reason_codes, list)
            or any(
                not isinstance(reason, str)
                or _SAFE_REASON_CODE.fullmatch(reason) is None
                or reason not in _TARGET_REASON_CODES
                for reason in reason_codes
            )
            or len(reason_codes) != len(set(reason_codes))
        ):
            raise ValueError("promotion report target reason codes are invalid")
        if not isinstance(row["hybrid"], dict):
            raise TypeError("promotion report provider diagnostics are invalid")
        hybrid = row["hybrid"]
        provider_identity_is_valid = (
            hybrid.get("provider") in {"", _EXPECTED_HYBRID_DIAGNOSTIC_PROVIDER}
            if row["provider_fallback"]
            else hybrid.get("provider") == _EXPECTED_HYBRID_DIAGNOSTIC_PROVIDER
        )
        if (
            set(hybrid) != _HYBRID_DIAGNOSTIC_KEYS
            or not provider_identity_is_valid
            or not isinstance(hybrid["cache_hit"], bool)
            or not isinstance(hybrid["deterministic_rejection_reason_codes"], list)
            or any(
                not isinstance(reason, str)
                or _SAFE_REASON_CODE.fullmatch(reason) is None
                or reason not in _DETERMINISTIC_REASON_CODES
                for reason in hybrid["deterministic_rejection_reason_codes"]
            )
            or len(hybrid["deterministic_rejection_reason_codes"])
            != len(set(hybrid["deterministic_rejection_reason_codes"]))
        ):
            raise ValueError("promotion report hybrid diagnostics are malformed")
        for field in _HYBRID_DIAGNOSTIC_KEYS - {
            "provider",
            "cache_hit",
            "deterministic_rejection_reason_codes",
        }:
            _require_count(hybrid[field], f"hybrid {field}")
        if not row["provider_fallback"]:
            # Every sealed case owns a globally unique target entry, so its
            # case-local snapshot/cache identity is unique in this protocol.
            # A cache hit would prove that the row was not independently run.
            if hybrid["cache_hit"] is True:
                raise ValueError(
                    "promotion report canonical row cannot reuse adjudication cache"
                )
            if hybrid["candidate_count"] != len(candidates):
                raise ValueError(
                    "promotion report hybrid candidate count disagrees with row"
                )
            if hybrid["accepted_count"] != len(admitted):
                raise ValueError(
                    "promotion report hybrid accepted count disagrees with row"
                )
            if not _hybrid_stage_counts_are_coherent(hybrid):
                raise ValueError(
                    "promotion report hybrid stage accounting is inconsistent"
                )
            rejection_reason_codes = hybrid["deterministic_rejection_reason_codes"]
            if (
                hybrid["deterministic_rejected_count"] == 0 and rejection_reason_codes
            ) or (
                hybrid["deterministic_rejected_count"] > 0
                and (
                    not rejection_reason_codes
                    or len(rejection_reason_codes)
                    > hybrid["deterministic_rejected_count"]
                )
            ):
                raise ValueError(
                    "promotion report deterministic rejection evidence disagrees"
                )

        positive = row["polarity"] == "positive"
        candidate_hits += int(positive and row["target_candidate"])
        admitted_hits += int(positive and row["target_admitted"])
        rendered_hits += int(positive and row["target_rendered"])
        if positive:
            false_positive_admissions += sum(
                entry_id != row["target_id"] for entry_id in admitted
            )
            false_positive_renders += sum(
                entry_id != row["target_id"] for entry_id in rendered
            )
        else:
            false_positive_admissions += len(admitted)
            false_positive_renders += len(rendered)
            hard_negative_target_admissions += int(row["target_admitted"])
            hard_negative_target_renders += int(row["target_rendered"])
        lifecycle_leakages += len(lifecycle_leakage)
        safety_leakages += len(safety_leakage)
        forbidden_leakages += len(forbidden_leakage)
        provider_fallbacks += int(row["provider_fallback"])
        id_mismatches += len(foreign)

    if set(dense_by_id) != seen:
        raise ValueError("promotion report canonical IDs do not join exactly")
    counts = canonical["counts"]
    metrics = canonical["metrics"]
    expected_count_fields = {
        "positive_total",
        "hard_negative_total",
        "candidate_positive_hits",
        "admitted_positive_hits",
        "rendered_positive_hits",
        "false_positive_admissions",
        "false_positive_renders",
        "hard_negative_target_admissions",
        "hard_negative_target_renders",
        "lifecycle_leakage_count",
        "safety_leakage_count",
        "forbidden_leakage_count",
        "forbidden_indexed_count",
        "provider_fallback_count",
        "id_mismatch_count",
    }
    if not isinstance(counts, dict) or set(counts) != expected_count_fields:
        raise ValueError("promotion report canonical count schema mismatch")
    for field in expected_count_fields:
        _require_count(counts[field], field)
    _require_exact_number(
        counts["positive_total"], EXPECTED_POSITIVE_COUNT, "positive denominator"
    )
    _require_exact_number(
        counts["hard_negative_total"],
        EXPECTED_HARD_NEGATIVE_COUNT,
        "hard-negative denominator",
    )
    recomputed = {
        "candidate_positive_hits": candidate_hits,
        "admitted_positive_hits": admitted_hits,
        "rendered_positive_hits": rendered_hits,
        "false_positive_admissions": false_positive_admissions,
        "false_positive_renders": false_positive_renders,
        "hard_negative_target_admissions": hard_negative_target_admissions,
        "hard_negative_target_renders": hard_negative_target_renders,
        "lifecycle_leakage_count": lifecycle_leakages,
        "safety_leakage_count": safety_leakages,
        "forbidden_leakage_count": forbidden_leakages,
        "provider_fallback_count": provider_fallbacks,
        "id_mismatch_count": id_mismatches,
    }
    if any(counts[field] != value for field, value in recomputed.items()):
        raise ValueError("promotion report canonical counts disagree with case rows")

    forbidden_ids = _require_unique_id_list(
        canonical["forbidden_indexed_ids"], "forbidden-indexed"
    )
    if len(forbidden_ids) != counts["forbidden_indexed_count"]:
        raise ValueError("promotion report forbidden-index count disagrees")
    expected_metrics = {
        "canonical_candidate_positive_recall": candidate_hits / EXPECTED_POSITIVE_COUNT,
        "admitted_positive_recall": admitted_hits / EXPECTED_POSITIVE_COUNT,
        "admitted_precision": admitted_hits
        / max(1, admitted_hits + false_positive_admissions),
        "rendered_positive_recall": rendered_hits / EXPECTED_POSITIVE_COUNT,
        "rendered_precision": rendered_hits
        / max(1, rendered_hits + false_positive_renders),
        "hard_negative_target_admission_rate": hard_negative_target_admissions
        / EXPECTED_HARD_NEGATIVE_COUNT,
        "hard_negative_admission_one_sided_exact_95_upper": (
            one_sided_exact_binomial_upper(
                hard_negative_target_admissions,
                EXPECTED_HARD_NEGATIVE_COUNT,
            )
        ),
        "hard_negative_target_render_rate": hard_negative_target_renders
        / EXPECTED_HARD_NEGATIVE_COUNT,
        "hard_negative_one_sided_exact_95_upper": one_sided_exact_binomial_upper(
            hard_negative_target_renders, EXPECTED_HARD_NEGATIVE_COUNT
        ),
    }
    if not isinstance(metrics, dict) or set(metrics) != set(expected_metrics):
        raise ValueError("promotion report canonical metric schema mismatch")
    for field, expected in expected_metrics.items():
        if _require_rate(metrics[field], field) != expected:
            raise ValueError("promotion report canonical metrics disagree with rows")


def validate_content_free_promotion_report(report: Any) -> dict[str, Any]:
    """Validate publishable structure for both passing and failing reports."""

    if not isinstance(report, dict) or set(report) != _REPORT_KEYS:
        raise ValueError("promotion report schema mismatch")
    if _contains_sealed_content_key(report):
        raise ValueError("promotion report leaks sealed content")
    fingerprint = report.get("report_fingerprint")
    if not _is_sha256(fingerprint) or fingerprint != evidence_fingerprint(report):
        raise ValueError("promotion report fingerprint mismatch")
    if (
        report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("protocol_version") != HYBRID_PROTOCOL_VERSION
        or report.get("synthetic_data") is not True
        or report.get("one_shot") is not True
        or report.get("embedding_provider") != "qwen"
        or report.get("remote_memory_authorized") is not True
        or report.get("inference_profile") != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
        or report.get("query_gate_version") != HYBRID_QUERY_GATE_VERSION
        or report.get("dense_top_k") != DEFAULT_DENSE_TOP_K
        or report.get("max_union_candidates") != MAX_CANDIDATES
        or report.get("max_model_calls_per_task") != DEFAULT_MAX_MODEL_CALLS_PER_TASK
        or report.get("thresholds") != PROMOTION_THRESHOLDS
        or report.get("candidate_corpus_protocol")
        != "global_all_eligible_164_max_entries"
        or report.get("canonical_corpus_protocol")
        != "case_target_plus_80_shared_background"
    ):
        raise ValueError("promotion report protocol contract mismatch")

    validate_promotion_one_shot_claim(report.get("one_shot_claim"))
    if report.get("implementation_freeze") != build_implementation_freeze():
        raise ValueError("promotion report implementation freeze mismatch")
    if report.get("runtime_implementation_freeze") != build_hybrid_runtime_freeze(ROOT):
        raise ValueError("promotion report runtime implementation freeze mismatch")
    validate_resolver_pressure_receipt(report.get("resolver_pressure_receipt"))
    validate_qwen_identity(report.get("model"))
    if report.get("verifier_route") != qwen_verifier_route_identity():
        raise ValueError("promotion report verifier route mismatch")
    expected_decision = build_hybrid_decision_contract(DEFAULT_VERIFIER_MODEL)
    if {
        "verifier": report.get("verifier"),
        "challenger": report.get("challenger"),
        "resolver": report.get("resolver"),
    } != expected_decision:
        raise ValueError("promotion report verifier contract mismatch")

    holdout = report.get("holdout")
    expected_holdout = {
        "version": HOLDOUT_VERSION,
        "file_sha256": EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "background_count": EXPECTED_BACKGROUND_COUNT,
        "case_count": EXPECTED_CASE_COUNT,
        "positive_count": EXPECTED_POSITIVE_COUNT,
        "hard_negative_count": EXPECTED_HARD_NEGATIVE_COUNT,
    }
    if (
        not isinstance(holdout, dict)
        or set(holdout) != QWEN_PROMOTION_HOLDOUT_KEYS
        or holdout != expected_holdout
    ):
        raise ValueError("promotion report holdout identity mismatch")

    dense_by_id = _validate_dense_evidence(report.get("global_dense"))
    canonical = report.get("canonical")
    _validate_canonical_evidence(canonical, dense_by_id)
    assert isinstance(canonical, dict)
    canonical_counts = canonical["counts"]
    assert isinstance(canonical_counts, dict)
    _validate_usage_evidence(
        report.get("usage"),
        canonical=canonical,
        allow_incomplete_model_lanes=canonical_counts["provider_fallback_count"] > 0,
    )
    gate = report.get("acceptance_gate")
    if not isinstance(gate, dict) or set(gate) != {"passed", "checks"}:
        raise ValueError("promotion report acceptance gate schema mismatch")
    checks = gate.get("checks")
    if (
        not isinstance(checks, dict)
        or set(checks) != _PROMOTION_CHECK_KEYS
        or any(not isinstance(value, bool) for value in checks.values())
    ):
        raise ValueError("promotion report acceptance checks schema mismatch")
    recomputed = acceptance_checks(report)
    if checks != recomputed or gate.get("passed") != all(recomputed.values()):
        raise ValueError("promotion report acceptance gate disagrees with evidence")
    return report


def validate_promotion_report(report: Any) -> dict[str, Any]:
    """Recompute the full production authorization contract from raw counts."""
    validate_content_free_promotion_report(report)
    if not isinstance(report, dict):
        raise TypeError("promotion report must be an object")
    if (
        set(report) != _REPORT_KEYS
        or report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("protocol_version") != HYBRID_PROTOCOL_VERSION
        or report.get("synthetic_data") is not True
        or report.get("one_shot") is not True
        or report.get("embedding_provider") != "qwen"
        or report.get("remote_memory_authorized") is not True
        or report.get("inference_profile") != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
        or report.get("query_gate_version") != HYBRID_QUERY_GATE_VERSION
        or report.get("dense_top_k") != DEFAULT_DENSE_TOP_K
        or report.get("max_union_candidates") != MAX_CANDIDATES
        or report.get("max_model_calls_per_task") != DEFAULT_MAX_MODEL_CALLS_PER_TASK
        or report.get("thresholds") != PROMOTION_THRESHOLDS
        or report.get("candidate_corpus_protocol")
        != "global_all_eligible_164_max_entries"
        or report.get("canonical_corpus_protocol")
        != "case_target_plus_80_shared_background"
    ):
        raise ValueError("promotion report protocol contract mismatch")
    fingerprint = report.get("report_fingerprint")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or fingerprint != evidence_fingerprint(report)
    ):
        raise ValueError("promotion report fingerprint mismatch")
    validate_qwen_identity(report.get("model"))
    implementation_freeze = report.get("implementation_freeze")
    if implementation_freeze != build_implementation_freeze():
        raise ValueError("promotion report implementation freeze mismatch")
    if report.get("runtime_implementation_freeze") != build_hybrid_runtime_freeze(ROOT):
        raise ValueError("promotion report runtime implementation freeze mismatch")
    require_resolver_pressure_release_anchor(report.get("resolver_pressure_receipt"))
    validate_promotion_one_shot_claim(report.get("one_shot_claim"))

    holdout = report.get("holdout")
    if not isinstance(holdout, dict):
        raise TypeError("promotion report holdout evidence is missing")
    if (
        holdout.get("version") != HOLDOUT_VERSION
        or holdout.get("file_sha256") != EXPECTED_HOLDOUT_SHA256
        or holdout.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
    ):
        raise ValueError("promotion report holdout identity mismatch")
    _require_exact_number(
        holdout.get("background_count"), EXPECTED_BACKGROUND_COUNT, "background count"
    )
    _require_exact_number(holdout.get("case_count"), EXPECTED_CASE_COUNT, "case count")
    _require_exact_number(
        holdout.get("positive_count"), EXPECTED_POSITIVE_COUNT, "positive count"
    )
    _require_exact_number(
        holdout.get("hard_negative_count"),
        EXPECTED_HARD_NEGATIVE_COUNT,
        "hard-negative count",
    )

    verifier = report.get("verifier")
    challenger = report.get("challenger")
    resolver = report.get("resolver")
    route = report.get("verifier_route")
    if (
        not isinstance(verifier, dict)
        or not isinstance(challenger, dict)
        or not isinstance(resolver, dict)
    ):
        raise TypeError("promotion report decision evidence is missing")
    verifier_model = verifier.get("model_id")
    expected = _evaluation_evidence(
        report["model"],
        str(verifier_model),
        report["resolver_pressure_receipt"],
        report["one_shot_claim"],
    )
    if (
        verifier_model != DEFAULT_VERIFIER_MODEL
        or verifier != expected["verifier"]
        or challenger != expected["challenger"]
        or resolver != expected["resolver"]
        or route != qwen_verifier_route_identity()
    ):
        raise ValueError("promotion report verifier contract mismatch")

    if _contains_sealed_content_key(report):
        raise ValueError("promotion report leaks sealed content")
    dense = report.get("global_dense")
    canonical = report.get("canonical")
    dense_by_id = _validate_dense_evidence(dense)
    _validate_canonical_evidence(canonical, dense_by_id)
    assert isinstance(canonical, Mapping)
    _validate_usage_evidence(report.get("usage"), canonical=canonical)
    checks = acceptance_checks(report)
    gate = report.get("acceptance_gate")
    if (
        not isinstance(gate, dict)
        or gate.get("checks") != checks
        or gate.get("passed") is not True
        or not all(checks.values())
    ):
        raise ValueError("a failing report cannot authorize production use")
    return report


def build_qwen_v2_production_evidence(
    report: dict[str, Any],
    *,
    promotion_artifact: str = REPORT_ARTIFACT,
) -> dict[str, Any]:
    artifact = PurePosixPath(str(promotion_artifact))
    if (
        artifact.is_absolute()
        or ".." in artifact.parts
        or artifact.suffix != ".json"
        or not artifact.parts
    ):
        raise ValueError("production report artifact reference is unsafe")
    if artifact.as_posix() != REPORT_ARTIFACT:
        raise ValueError("production report artifact is fixed for the one-shot run")
    validated = validate_promotion_report(report)
    payload = {
        "schema_version": HYBRID_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot_claim": validate_promotion_one_shot_claim(
            validated["one_shot_claim"]
        ),
        "promotion_report": {
            "artifact": artifact.as_posix(),
            "report_fingerprint": validated["report_fingerprint"],
        },
        "holdout": dict(validated["holdout"]),
        "implementation_freeze": dict(validated["implementation_freeze"]),
        "runtime_implementation_freeze": dict(
            validated["runtime_implementation_freeze"]
        ),
        "resolver_pressure_receipt": require_resolver_pressure_release_anchor(
            validated["resolver_pressure_receipt"]
        ),
        "model": dict(validated["model"]),
        "embedding_provider": "qwen",
        "verifier_route": qwen_verifier_route_identity(),
        "inference_profile": dict(validated["inference_profile"]),
        "verifier": dict(validated["verifier"]),
        "challenger": dict(validated["challenger"]),
        "resolver": dict(validated["resolver"]),
        "query_gate_version": HYBRID_QUERY_GATE_VERSION,
        "dense_top_k": DEFAULT_DENSE_TOP_K,
        "max_union_candidates": MAX_CANDIDATES,
        "max_model_calls_per_task": DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        "acceptance_gate": dict(validated["acceptance_gate"]),
        "production_enablement_allowed": True,
    }
    payload["report_fingerprint"] = evidence_fingerprint(payload)
    if set(payload) != QWEN_PRODUCTION_EVIDENCE_KEYS:
        raise AssertionError("Qwen production evidence schema changed")
    return payload


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Publish complete JSON through one anchored directory, without replacement."""

    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
    parent = path.parent
    try:
        metadata = os.lstat(parent)
    except OSError as exc:
        raise ValueError("artifact parent must already exist") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("artifact parent must be a non-symlink directory")
    if (
        os.open not in os.supports_dir_fd
        or os.link not in os.supports_dir_fd
        or os.unlink not in os.supports_dir_fd
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise RuntimeError("secure artifact publication is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    parent_descriptor = os.open(parent, flags)
    opened = os.fstat(parent_descriptor)
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(parent_descriptor)
        raise ValueError("artifact parent changed during validation")

    temporary_descriptor: int | None = None
    published_descriptor: int | None = None
    try:
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        create_flags |= getattr(os, "O_CLOEXEC", 0)
        temporary_descriptor = os.open(
            temporary_name,
            create_flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("artifact publication made no write progress")
            view = view[written:]
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published_flags = os.O_RDONLY | os.O_NOFOLLOW
        published_flags |= getattr(os, "O_CLOEXEC", 0)
        published_descriptor = os.open(
            path.name,
            published_flags,
            dir_fd=parent_descriptor,
        )
        published_metadata = os.fstat(published_descriptor)
        if (
            not stat.S_ISREG(published_metadata.st_mode)
            or (
                published_metadata.st_dev,
                published_metadata.st_ino,
                published_metadata.st_size,
            )
            != (
                temporary_metadata.st_dev,
                temporary_metadata.st_ino,
                temporary_metadata.st_size,
            )
            or published_metadata.st_size != len(encoded)
        ):
            raise RuntimeError("published artifact inode changed before verification")
        os.fsync(published_descriptor)
        os.fsync(parent_descriptor)
    except FileExistsError as exc:
        raise FileExistsError(f"artifact already exists: {path}") from exc
    finally:
        if published_descriptor is not None:
            os.close(published_descriptor)
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except OSError:
            pass
        os.close(parent_descriptor)


def persist_report_and_optional_evidence(
    report: dict[str, Any],
    *,
    report_path: Path,
    evidence_path: Path,
) -> Path | None:
    """Always persist a completed report; authorize only a fully valid pass."""
    validate_content_free_promotion_report(report)
    _write_json_exclusive(report_path, report)
    gate = report.get("acceptance_gate")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        return None
    try:
        promotion_artifact = (
            report_path.resolve().relative_to(ROOT.resolve()).as_posix()
        )
    except ValueError as exc:
        raise ValueError(
            "passing promotion report must be published inside the repository"
        ) from exc
    evidence = build_qwen_v2_production_evidence(
        report,
        promotion_artifact=promotion_artifact,
    )
    _write_json_exclusive(evidence_path, evidence)
    return evidence_path


def run_evaluation(
    *,
    env_file: Path = ROOT / ".env",
) -> dict[str, Any]:
    """Run the sole authoritative promotion route with preregistered identities."""
    # Validate every local, deterministic precondition before consuming the
    # cooperative one-shot marker. No provider call occurs in this phase.
    implementation_freeze = build_implementation_freeze()
    runtime_implementation_freeze = build_hybrid_runtime_freeze(ROOT)
    pressure_receipt = load_resolver_pressure_report(
        DEFAULT_RESOLVER_PRESSURE_REPORT,
        artifact_reference=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    )
    frozen = load_frozen_holdout(DEFAULT_HOLDOUT)

    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_dedicated_model_adapter

    apply_env_file([env_file])
    runtime = load_runtime_config(ROOT)
    model = create_exact_subagent_verifier(
        runtime,
        DEFAULT_VERIFIER_MODEL,
        adapter_factory=create_dedicated_model_adapter,
    )
    raw_client = create_openai_compatible_embedding_client(ROOT)
    if raw_client is None:
        raise RuntimeError("Qwen embedding configuration is unavailable")
    usage_sink = UsageCollector()
    client = _ObservedEmbeddingClient(raw_client, usage_sink)
    encoder = OpenAICompatibleEmbeddingEncoder(client, provider="qwen")
    # Claim immediately before ``identity`` makes the first remote embedding
    # request. Hash/schema/constructibility and exact model routes above can
    # therefore fail safely without spending the sealed evaluation attempt.
    one_shot_claim = asdict(
        claim_evaluation_run(
            DEFAULT_ONE_SHOT_MARKER,
            dataset_id=HOLDOUT_VERSION,
            holdout_sha256=EXPECTED_HOLDOUT_SHA256,
            output_artifact=REPORT_ARTIFACT,
        )
    )
    failure_stage = "embedding_identity"
    try:
        identity = validate_qwen_identity(encoder.identity)
        failure_stage = "global_dense"
        global_dense = evaluate_global_dense(
            frozen.dataset,
            encoder=encoder,
            usage_sink=usage_sink,
        )
        failure_stage = "canonical_retrieval"
        with tempfile.TemporaryDirectory(
            prefix="minicode-hybrid-qwen-v2-"
        ) as temporary:
            canonical = evaluate_canonical(
                frozen.dataset,
                model=model,
                client=client,
                identity=identity,
                verifier_model=DEFAULT_VERIFIER_MODEL,
                resolver_pressure_receipt=pressure_receipt,
                one_shot_claim=one_shot_claim,
                workspace=Path(temporary) / "workspace",
                usage_sink=usage_sink,
            )
        failure_stage = "report_build"
        report = build_report(
            frozen=frozen,
            implementation_freeze=implementation_freeze,
            runtime_implementation_freeze=runtime_implementation_freeze,
            resolver_pressure_receipt=pressure_receipt,
            one_shot_claim=one_shot_claim,
            identity=identity,
            verifier_model=DEFAULT_VERIFIER_MODEL,
            global_dense=global_dense,
            canonical=canonical,
            usage=usage_sink.snapshot(),
        )
        validate_content_free_promotion_report(report)
        return report
    except BaseException as error:  # a claimed attempt must remain auditable
        if isinstance(error, (SystemExit, GeneratorExit)):
            raise
        failure_kind = _classify_post_claim_failure(
            error,
            stage=failure_stage,
        )
        failure_report = build_post_claim_failure_report(
            frozen=frozen,
            implementation_freeze=implementation_freeze,
            runtime_implementation_freeze=runtime_implementation_freeze,
            resolver_pressure_receipt=pressure_receipt,
            one_shot_claim=one_shot_claim,
            failure_stage=failure_stage,
            failure_kind=failure_kind,
            usage=usage_sink.snapshot(),
        )
        raise PromotionEvaluationRunFailed(failure_report) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument(
        "--resolver-pressure-report",
        type=Path,
        default=DEFAULT_RESOLVER_PRESSURE_REPORT,
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--verifier-model", default=DEFAULT_VERIFIER_MODEL)
    parser.add_argument("--output", type=Path, default=ROOT / REPORT_ARTIFACT)
    parser.add_argument(
        "--production-evidence-output",
        type=Path,
        default=ROOT / PRODUCTION_EVIDENCE_ARTIFACT,
    )
    parser.add_argument("--acknowledge-one-shot", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_one_shot:
        raise SystemExit("independent sealed holdout requires --acknowledge-one-shot")
    exact_paths = {
        "holdout": (args.holdout, DEFAULT_HOLDOUT),
        "resolver pressure report": (
            args.resolver_pressure_report,
            DEFAULT_RESOLVER_PRESSURE_REPORT,
        ),
        "report output": (args.output, ROOT / REPORT_ARTIFACT),
        "production evidence output": (
            args.production_evidence_output,
            ROOT / PRODUCTION_EVIDENCE_ARTIFACT,
        ),
    }
    for label, (actual, expected) in exact_paths.items():
        if actual.expanduser().resolve() != expected.resolve():
            raise SystemExit(f"{label} is fixed for the authoritative one-shot run")
    if args.verifier_model != DEFAULT_VERIFIER_MODEL:
        raise SystemExit("verifier model is fixed for the authoritative one-shot run")
    if args.output.exists() or args.production_evidence_output.exists():
        raise SystemExit("one-shot report or production evidence already exists")

    try:
        report = run_evaluation(
            env_file=args.env_file,
        )
    except PromotionEvaluationRunFailed as failure:
        _write_json_exclusive(args.output, failure.failure_report)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "production_evidence_output": None,
                    "failure_stage": failure.failure_report["failure_stage"],
                    "failure_kind": failure.failure_report["failure_kind"],
                    "report_fingerprint": failure.failure_report[
                        "report_fingerprint"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2) from None
    evidence_path = persist_report_and_optional_evidence(
        report,
        report_path=args.output,
        evidence_path=args.production_evidence_output,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "production_evidence_output": (
                    str(evidence_path) if evidence_path is not None else None
                ),
                "model": report["model"],
                "verifier_model": args.verifier_model,
                "candidate_recall_at_20": report["global_dense"][
                    "candidate_recall_at_20"
                ],
                "category_macro_recall_at_20": report["global_dense"][
                    "category_macro_recall_at_20"
                ],
                **report["canonical"]["metrics"],
                "usage": report["usage"],
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
