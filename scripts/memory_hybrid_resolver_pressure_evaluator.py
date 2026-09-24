"""One-shot safety gate for the proof-carrying Hybrid Memory resolver.

The sealed panel contains only hard-negative query/memory pairs.  Every pair
is deliberately presented as a valid primary/challenger disagreement so this
script measures the resolver itself rather than easier upstream vetoes.  The
report is content-free and never authorizes production on its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from minicode.evaluation_one_shot import (
    claim_evaluation_run,
    validate_evaluation_one_shot_receipt,
)
from minicode.memory import MemoryEntry, MemoryScope, MemoryTier
from minicode.memory_hybrid import (
    HYBRID_PROMOTION_INFERENCE_PROFILE,
    HYBRID_PROTOCOL_VERSION,
    HYBRID_RESOLVER_PRESSURE_ARTIFACT,
    HYBRID_RESOLVER_PRESSURE_CASE_COUNT,
    HYBRID_RESOLVER_PRESSURE_DATASET_ID,
    HYBRID_RESOLVER_PRESSURE_HOLDOUT_SHA256,
    HYBRID_RESOLVER_PRESSURE_MANIFEST_SHA256,
    HYBRID_RESOLVER_SYSTEM_PROMPT,
    build_hybrid_decision_contract,
    build_hybrid_promotion_verifier_runtime,
    evidence_fingerprint,
)
from minicode.memory_hybrid_freeze import HYBRID_RUNTIME_BEHAVIOR_FILES
from minicode.memory_hybrid_promotion_contract import qwen_verifier_route_identity
from minicode.memory_hybrid_runtime import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_MODEL_CALLS_PER_TASK,
    HybridResponseProtocolError,
    HybridRuntimeProvider,
    _build_resolver_batch,
    _parse_resolutions,
    _resolution_is_approved,
    _stable_json,
)
from minicode.memory_retrieval import MemoryRetrievalRequest
from scripts.memory_hybrid_qwen_v2_evaluator import (
    _write_json_exclusive,
    create_exact_subagent_verifier,
    validate_resolver_pressure_report,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET_ID = HYBRID_RESOLVER_PRESSURE_DATASET_ID
DEFAULT_HOLDOUT = (
    ROOT
    / ".repair-work"
    / "memory-retrieval-quality-2026-09-03"
    / DATASET_ID
    / "holdout.json"
)
DEFAULT_OUTPUT = ROOT / HYBRID_RESOLVER_PRESSURE_ARTIFACT
ONE_SHOT_MARKER_NAME = ".resolver-pressure-v5.consumed.json"
DEFAULT_ONE_SHOT_MARKER = DEFAULT_HOLDOUT.parent / ONE_SHOT_MARKER_NAME
EXPECTED_HOLDOUT_SHA256 = HYBRID_RESOLVER_PRESSURE_HOLDOUT_SHA256
EXPECTED_MANIFEST_SHA256 = HYBRID_RESOLVER_PRESSURE_MANIFEST_SHA256
EXPECTED_CASE_COUNT = HYBRID_RESOLVER_PRESSURE_CASE_COUNT
EXPECTED_CATEGORY_COUNT = 12
EXPECTED_CASES_PER_CATEGORY = 5
DEFAULT_VERIFIER_MODEL = "qwen3.7-plus"
REPORT_SCHEMA_VERSION = "resolver-pressure-report-v5"
POST_CLAIM_FAILURE_SCHEMA_VERSION = "resolver-pressure-failure-v1"
IMPLEMENTATION_FREEZE_VERSION = "resolver-pressure-freeze-v6"
MAX_HOLDOUT_BYTES = 1_000_000
REFERENCE_TIME = 1_800_000_000.0
ONE_SIDED_ALPHA = 0.05

_POST_CLAIM_FAILURE_STAGES = frozenset(
    {"evaluate_resolver", "build_report", "validate_report"}
)
_POST_CLAIM_FAILURE_KINDS = frozenset(
    {"post_claim_validation_error", "provider_or_transport_error", "unexpected_error"}
)
_POST_CLAIM_FAILURE_KEYS = frozenset(
    {
        "schema_version",
        "protocol_version",
        "synthetic_data",
        "one_shot",
        "production_authority",
        "one_shot_claim",
        "holdout",
        "implementation_freeze",
        "inference_profile",
        "verifier_route",
        "failure_stage",
        "failure_kind",
        "acceptance_gate",
        "report_fingerprint",
    }
)

BEHAVIOR_CRITICAL_FILES = (
    "scripts/memory_hybrid_resolver_pressure_evaluator.py",
    "scripts/memory_hybrid_qwen_v2_evaluator.py",
    *HYBRID_RUNTIME_BEHAVIOR_FILES,
)

_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "dataset_id", "created_on", "sealed", "item_count", "items"}
)
_ITEM_KEYS = frozenset(
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
_SAFE_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

_CONTESTED_REASON = {
    "actor_swap": "different_actor",
    "cross_language_same_topic_different_object": "different_object",
    "deprecated_or_version_mismatch": "unsupported_relation",
    "exact_file_different_operation": "unsupported_relation",
    "fabricated_recovery": "unsupported_relation",
    "metadata_body_contradiction": "unsupported_relation",
    "multi_candidate_same_topic_no_exact_match": "different_object",
    "order_or_polarity_reversal": "unsupported_relation",
    "prompt_or_json_injection": "unrelated",
    "same_symptom_different_recovery": "unsupported_relation",
    "same_symptom_different_root_cause": "unsupported_relation",
    "unicode_or_zero_width_identifier_collision": "different_object",
}


class _UnusedEncoder:
    """Constructor dependency that must never run in resolver-only pressure."""

    def encode_documents(self, _texts: Any, **_kwargs: Any) -> Any:
        raise AssertionError("resolver pressure must not call the embedding encoder")

    def encode_queries(self, _texts: Any, **_kwargs: Any) -> Any:
        raise AssertionError("resolver pressure must not call the embedding encoder")


class ResolverRouteCanaryError(RuntimeError):
    """Content-free pre-claim failure from the fixed public route canary."""

    _ALLOWED_REASONS = frozenset(
        {
            "provider_or_transport_error",
            "response_protocol_error",
            "semantic_canary_failure",
        }
    )

    def __init__(self, reason: str) -> None:
        if reason not in self._ALLOWED_REASONS:
            reason = "provider_or_transport_error"
        self.reason = reason
        super().__init__(f"resolver route canary failed: {reason}")


class ResolverProviderTransportFailure(RuntimeError):
    """Content-free post-claim provider failure bound to one batch ordinal."""

    def __init__(self, batch_ordinal: int) -> None:
        self.batch_ordinal = int(batch_ordinal)
        super().__init__(
            f"resolver provider or transport failed for batch {self.batch_ordinal}"
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _report_identifier(namespace: str, value: str) -> str:
    return _sha256_bytes(f"resolver-pressure-id-v1\0{namespace}\0{value}".encode())


def _read_json_no_duplicates(raw: bytes) -> Any:
    def build_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
            object_pairs_hook=build_object,
            parse_constant=reject_non_finite,
        )
    except (RecursionError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("resolver holdout is not strict UTF-8 JSON") from exc


def _require_string_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 32
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field} must be a bounded unique string list")
    return list(value)


def validate_dataset(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TOP_LEVEL_KEYS:
        raise ValueError("resolver holdout top-level schema mismatch")
    if (
        value["schema_version"] != "1.0.0"
        or value["dataset_id"] != DATASET_ID
        or value["sealed"] is not True
        or value["item_count"] != EXPECTED_CASE_COUNT
        or not isinstance(value["created_on"], str)
    ):
        raise ValueError("resolver holdout identity mismatch")
    items = value["items"]
    if not isinstance(items, list) or len(items) != EXPECTED_CASE_COUNT:
        raise ValueError("resolver holdout case count mismatch")
    seen_items: set[str] = set()
    seen_memories: set[str] = set()
    category_counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
            raise ValueError("resolver holdout item schema mismatch")
        item_id = item["id"]
        relation = item["relation"]
        if (
            not isinstance(item_id, str)
            or _SAFE_SOURCE_ID.fullmatch(item_id) is None
            or item_id in seen_items
            or not isinstance(item["query"], str)
            or not item["query"].strip()
            or item["expected_relevant"] is not False
            or item["polarity"] != "negative"
            or relation not in _CONTESTED_REASON
        ):
            raise ValueError("resolver holdout item identity is invalid")
        _require_string_list(item["current_files"], "current_files")
        _require_string_list(item["active_domains"], "active_domains")
        entry = item["entry"]
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise ValueError("resolver holdout entry schema mismatch")
        entry_id = entry["id"]
        if (
            not isinstance(entry_id, str)
            or _SAFE_SOURCE_ID.fullmatch(entry_id) is None
            or entry_id in seen_memories
            or not isinstance(entry["memory_text"], str)
            or not entry["memory_text"].strip()
            or not isinstance(entry["metadata"], dict)
        ):
            raise ValueError("resolver holdout entry identity is invalid")
        _require_string_list(entry["domains"], "entry.domains")
        _require_string_list(entry["stored_files"], "entry.stored_files")
        seen_items.add(item_id)
        seen_memories.add(entry_id)
        category_counts[relation] = category_counts.get(relation, 0) + 1
    if len(category_counts) != EXPECTED_CATEGORY_COUNT or set(category_counts) != set(
        _CONTESTED_REASON
    ):
        raise ValueError("resolver holdout category coverage mismatch")
    if set(category_counts.values()) != {EXPECTED_CASES_PER_CATEGORY}:
        raise ValueError("resolver holdout category balance mismatch")
    return value


def load_frozen_holdout(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("resolver holdout path is unsafe or missing")
    raw = candidate.read_bytes()
    if len(raw) > MAX_HOLDOUT_BYTES:
        raise ValueError("resolver holdout is oversized")
    holdout_sha = _sha256_bytes(raw)
    if holdout_sha != EXPECTED_HOLDOUT_SHA256:
        raise ValueError("resolver holdout hash mismatch")
    manifest_path = candidate.with_name("MANIFEST.sha256")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("resolver holdout manifest is unsafe or missing")
    manifest_raw = manifest_path.read_bytes()
    manifest_sha = _sha256_bytes(manifest_raw)
    if manifest_sha != EXPECTED_MANIFEST_SHA256:
        raise ValueError("resolver holdout manifest hash mismatch")
    try:
        manifest_text = manifest_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("resolver holdout manifest is not UTF-8") from exc
    bound_rows = [
        row
        for row in manifest_text.splitlines()
        if row.split() == [EXPECTED_HOLDOUT_SHA256, candidate.name]
    ]
    if len(bound_rows) != 1:
        raise ValueError("resolver manifest does not bind holdout.json exactly once")
    return validate_dataset(_read_json_no_duplicates(raw)), {
        "dataset_id": DATASET_ID,
        "holdout_sha256": holdout_sha,
        "manifest_sha256": manifest_sha,
        "case_count": EXPECTED_CASE_COUNT,
    }


def implementation_freeze() -> dict[str, Any]:
    files: dict[str, str] = {}
    for relative in BEHAVIOR_CRITICAL_FILES:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"behavior-critical file is missing or unsafe: {relative}")
        files[relative] = _sha256_bytes(path.read_bytes())
    payload = {"schema_version": IMPLEMENTATION_FREEZE_VERSION, "files": files}
    payload["fingerprint"] = evidence_fingerprint(payload)
    return payload


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key, [])
    return _require_string_list(value, f"metadata.{key}") if value else []


def _memory_entry(source: dict[str, Any], relation: str) -> MemoryEntry:
    metadata = dict(source["metadata"])
    stored_files = list(source["stored_files"])
    metadata_files = _metadata_string_list(metadata, "file_paths")
    if stored_files or metadata_files:
        metadata["file_paths"] = list(dict.fromkeys((*metadata_files, *stored_files)))
    try:
        scope = MemoryScope(str(metadata.get("scope", "project")))
        tier = MemoryTier(str(metadata.get("tier", "long_term")))
    except ValueError as exc:
        raise ValueError("resolver entry scope or tier is invalid") from exc
    return MemoryEntry(
        id=source["id"],
        scope=scope,
        category=str(metadata.get("category") or relation),
        content=source["memory_text"],
        tags=_metadata_string_list(metadata, "tags"),
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
        source="synthetic-resolver-pressure-holdout",
    )


def _primary_reason(relation: str) -> str:
    if relation == "same_symptom_different_root_cause":
        return "cause"
    if relation in {"same_symptom_different_recovery", "fabricated_recovery"}:
        return "recovery"
    return "semantic_equivalence"


def one_sided_exact_binomial_upper(
    successes: int, trials: int, alpha: float = 0.05
) -> float:
    """Clopper-Pearson one-sided upper bound without a SciPy dependency."""

    if isinstance(successes, bool) or isinstance(trials, bool):
        raise TypeError("binomial counts must be integers")
    if not 0 <= successes <= trials or trials <= 0 or not 0.0 < alpha < 1.0:
        raise ValueError("invalid binomial interval inputs")
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

    low, high = successes / trials, 1.0
    for _ in range(100):
        middle = (low + high) / 2.0
        if cdf(middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def _step_usage(step: Any) -> dict[str, int]:
    usage = getattr(step, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def run_fixed_resolver_route_canary(model: Any) -> dict[str, Any]:
    """Exercise the exact resolver route before any one-shot claim is written.

    The prompt and pair are public constants owned by this source file.  The
    function accepts no dataset input, discards provider text, and exposes only
    a content-free receipt.  A fresh adapter is constructed for the sealed run
    after this canary succeeds.
    """

    entry = MemoryEntry(
        id="resolver-route-canary-memory-v1",
        scope=MemoryScope.PROJECT,
        category="performance",
        content="The image thumbnail cache expires after five seconds.",
        tags=["thumbnail", "cache"],
        domains=["media"],
        metadata={"component": "thumbnail-worker"},
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
        tier=MemoryTier.LONG_TERM,
        created_at=REFERENCE_TIME,
        updated_at=REFERENCE_TIME,
        last_accessed=REFERENCE_TIME,
        source="synthetic-resolver-route-canary",
    )
    request = MemoryRetrievalRequest(
        query="Raise the checkout retry limit for the payment coordinator.",
        current_files=("services/checkout/retry.py",),
        active_domains=("payments",),
    )
    decision = {
        "decision": "relevant",
        "confidence": 0.99,
        "objectMatch": True,
        "relationSupported": True,
        "reasonCode": "semantic_equivalence",
    }
    audit = {
        "admit": False,
        "confidence": 0.99,
        "reasonCode": "different_object",
    }
    pairs, expected = _build_resolver_batch(
        [(request, entry)],
        decisions={entry.id: decision},
        audits={entry.id: audit},
    )
    provider = HybridRuntimeProvider(
        encoder=_UnusedEncoder(),
        model_adapter=model,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        challenger_minimum_confidence=0.8,
        resolver_minimum_confidence=0.9,
        max_model_calls=1,
        embedding_provider="qwen",
    )
    try:
        step = provider._invoke_model(
            [
                {"role": "system", "content": HYBRID_RESOLVER_SYSTEM_PROMPT},
                {"role": "user", "content": _stable_json({"pairs": pairs})},
            ],
            purpose="memory_hybrid_resolver_route_canary",
            agent_budget=None,
            event_sink=None,
            cancellation_token=None,
            deadline_monotonic=None,
        )
    except HybridResponseProtocolError:
        raise ResolverRouteCanaryError("response_protocol_error") from None
    except Exception:
        raise ResolverRouteCanaryError("provider_or_transport_error") from None
    try:
        parsed = _parse_resolutions(step.content, expected=expected)
    except (TypeError, ValueError):
        raise ResolverRouteCanaryError("response_protocol_error") from None
    if (
        len(parsed) != 1
        or parsed[0]["id"] != entry.id
        or parsed[0]["decision"] != "irrelevant"
        or _resolution_is_approved(
            parsed[0],
            decision=decision,
            entry=entry,
            request=request,
            minimum_confidence=0.9,
        )
    ):
        raise ResolverRouteCanaryError("semantic_canary_failure")
    usage = _step_usage(step)
    return {
        "passed": True,
        "request_sha256": _sha256_bytes(_stable_json({"pairs": pairs}).encode()),
        "model_call_count": 1,
        "usage_available": bool(
            usage["input_tokens"] or usage["output_tokens"]
        ),
    }


def _protocol_failure_row(
    *,
    entry_id: str,
    item: dict[str, Any],
    decision: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Project a batch protocol failure without retaining model or sealed text.

    The row is deliberately a tagged, fail-closed rejection.  Spans come from
    the deterministic pair-bound input contract, not from untrusted model
    output, and therefore reveal no source text in the content-free report.
    """

    query_segment = next(iter(expected["query_segments"].values()))
    memory_segment = next(iter(expected["memory_segments"].values()))
    return {
        "case_id": _report_identifier("case", item["id"]),
        "entry_id": _report_identifier("entry", entry_id),
        "relation_sha256": _sha256_bytes(item["relation"].encode("utf-8")),
        "contested_reason": _CONTESTED_REASON[item["relation"]],
        "primary_reason": decision["reasonCode"],
        "decision": "irrelevant",
        "confidence": 0.0,
        "support_kind": "none",
        "disqualifier": _CONTESTED_REASON[item["relation"]],
        "query_span": {
            "start": query_segment["start"],
            "end": query_segment["end"],
        },
        "memory_span": {
            "start": memory_segment["start"],
            "end": memory_segment["end"],
        },
        "query_segment_kind": query_segment["kind"],
        "memory_segment_kind": memory_segment["kind"],
        "approved": False,
        "protocol_failure": True,
    }


def evaluate_resolver(
    dataset: dict[str, Any],
    model: Any,
    *,
    case_offset: int = 0,
    case_limit: int | None = None,
) -> dict[str, Any]:
    provider = HybridRuntimeProvider(
        encoder=_UnusedEncoder(),
        model_adapter=model,
        dense_top_k=20,
        max_candidates=32,
        minimum_confidence=0.85,
        challenger_minimum_confidence=0.8,
        resolver_minimum_confidence=0.9,
        max_model_calls=DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        embedding_provider="qwen",
    )
    rows: list[dict[str, Any]] = []
    total_usage = {"model_call_count": 0, "input_tokens": 0, "output_tokens": 0}
    protocol_failure_batches = 0
    protocol_failure_cases = 0
    all_items = dataset["items"]
    if (
        isinstance(case_offset, bool)
        or not isinstance(case_offset, int)
        or not 0 <= case_offset < len(all_items)
    ):
        raise ValueError("resolver development case offset is invalid")
    if case_limit is not None and (
        isinstance(case_limit, bool)
        or not 1 <= case_limit <= len(all_items) - case_offset
    ):
        raise ValueError("resolver development case limit is invalid")
    end = case_offset + case_limit if case_limit is not None else len(all_items)
    items = all_items[case_offset:end]
    for start in range(0, len(items), DEFAULT_BATCH_SIZE):
        batch = items[start : start + DEFAULT_BATCH_SIZE]
        entries: dict[str, MemoryEntry] = {}
        decisions: dict[str, dict[str, Any]] = {}
        requests: dict[str, MemoryRetrievalRequest] = {}
        item_by_entry_id: dict[str, dict[str, Any]] = {}
        for item in batch:
            entry = _memory_entry(item["entry"], item["relation"])
            request = MemoryRetrievalRequest(
                query=item["query"],
                current_files=tuple(item["current_files"]),
                active_domains=tuple(item["active_domains"]),
            )
            decision = {
                "decision": "relevant",
                "confidence": 0.99,
                "objectMatch": True,
                "relationSupported": True,
                "reasonCode": _primary_reason(item["relation"]),
            }
            entries[entry.id] = entry
            decisions[entry.id] = decision
            requests[entry.id] = request
            item_by_entry_id[entry.id] = item
        audits = {
            entry_id: {
                "admit": False,
                "confidence": 0.99,
                "reasonCode": _CONTESTED_REASON[item_by_entry_id[entry_id]["relation"]],
            }
            for entry_id in entries
        }
        pairs, expected = _build_resolver_batch(
            [(requests[entry_id], entries[entry_id]) for entry_id in entries],
            decisions=decisions,
            audits=audits,
        )
        # Count attempts before the call.  A provider/envelope failure can occur
        # after remote inference but before an AgentStep (and its usage) becomes
        # available to this evaluator.
        total_usage["model_call_count"] += 1
        batch_ordinal = start // DEFAULT_BATCH_SIZE + 1
        try:
            step = provider._invoke_model(
                [
                    {"role": "system", "content": HYBRID_RESOLVER_SYSTEM_PROMPT},
                    {"role": "user", "content": _stable_json({"pairs": pairs})},
                ],
                purpose="memory_hybrid_resolver_pressure",
                agent_budget=None,
                event_sink=None,
                cancellation_token=None,
                deadline_monotonic=None,
            )
        except HybridResponseProtocolError:
            protocol_failure_batches += 1
            protocol_failure_cases += len(batch)
            rows.extend(
                _protocol_failure_row(
                    entry_id=entry_id,
                    item=item_by_entry_id[entry_id],
                    decision=decisions[entry_id],
                    expected=expected[entry_id],
                )
                for entry_id in sorted(entries)
            )
            continue
        except Exception:
            # No trusted response exists.  Quota, authentication, network,
            # timeout, and provider failures invalidate the entire one-shot;
            # they must reach the outer post-claim failure artifact instead of
            # masquerading as 20 model protocol rejections.
            raise ResolverProviderTransportFailure(batch_ordinal) from None

        try:
            for key, value in _step_usage(step).items():
                total_usage[key] += value
            parsed = _parse_resolutions(step.content, expected=expected)
            by_id = {row["id"]: row for row in parsed}
            if len(by_id) != len(parsed) or set(by_id) != set(entries):
                raise ValueError("resolver pressure output IDs do not join exactly")
        except (TypeError, ValueError):
            # Preserve the one-shot evidence while matching production's
            # response-protocol fail-closed semantics. Never serialize
            # exception/model text: it can contain sealed content or
            # provider-controlled payloads.
            protocol_failure_batches += 1
            protocol_failure_cases += len(batch)
            rows.extend(
                _protocol_failure_row(
                    entry_id=entry_id,
                    item=item_by_entry_id[entry_id],
                    decision=decisions[entry_id],
                    expected=expected[entry_id],
                )
                for entry_id in sorted(entries)
            )
            continue

        # Parsed rows are now trusted closed-schema values.  Any later error is
        # an evaluator defect and must propagate to the post-claim failure
        # artifact instead of being mislabeled as model behavior.
        for entry_id in sorted(entries):
            resolution = by_id[entry_id]
            item = item_by_entry_id[entry_id]
            approved = _resolution_is_approved(
                resolution,
                decision=decisions[entry_id],
                entry=entries[entry_id],
                request=requests[entry_id],
                minimum_confidence=0.9,
            )
            rows.append(
                {
                    "case_id": _report_identifier("case", item["id"]),
                    "entry_id": _report_identifier("entry", entry_id),
                    "relation_sha256": _sha256_bytes(
                        item["relation"].encode("utf-8")
                    ),
                    "contested_reason": _CONTESTED_REASON[item["relation"]],
                    "primary_reason": decisions[entry_id]["reasonCode"],
                    "decision": resolution["decision"],
                    "confidence": float(resolution["confidence"]),
                    "support_kind": resolution["supportKind"],
                    "disqualifier": resolution["disqualifier"],
                    "query_span": dict(resolution["querySpan"]),
                    "memory_span": dict(resolution["memorySpan"]),
                    "query_segment_kind": resolution["querySegmentKind"],
                    "memory_segment_kind": resolution["memorySegmentKind"],
                    "approved": approved,
                    "protocol_failure": False,
                }
            )
    if len(rows) != len(items):
        raise ValueError("resolver pressure result count mismatch")
    raw_relevant = sum(row["decision"] == "relevant" for row in rows)
    approved = sum(row["approved"] for row in rows)
    return {
        "counts": {
            "case_count": len(rows),
            "raw_relevant_count": raw_relevant,
            "approved_count": approved,
            "protocol_failure_batches": protocol_failure_batches,
            "protocol_failure_cases": protocol_failure_cases,
        },
        "metrics": {
            "raw_relevant_rate": raw_relevant / len(rows),
            "approved_rate": approved / len(rows),
            "approved_one_sided_exact_95_upper": one_sided_exact_binomial_upper(
                approved, len(rows), ONE_SIDED_ALPHA
            ),
        },
        "usage": total_usage,
        "cases": rows,
    }


def _is_content_free(report: dict[str, Any], dataset: dict[str, Any]) -> bool:
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    protected = []
    for item in dataset["items"]:
        protected.extend((item["query"], item["entry"]["memory_text"]))
    return all(text not in serialized for text in protected if len(text) >= 8)


def build_report(
    *,
    dataset: dict[str, Any],
    holdout: dict[str, Any],
    freeze: dict[str, Any],
    one_shot_claim: dict[str, int | str],
    verifier_model: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    if verifier_model != DEFAULT_VERIFIER_MODEL:
        raise ValueError("resolver verifier model is not the preregistered route")
    validated_claim = validate_evaluation_one_shot_receipt(
        one_shot_claim,
        dataset_id=DATASET_ID,
        holdout_sha256=EXPECTED_HOLDOUT_SHA256,
        output_artifact=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        marker_name=ONE_SHOT_MARKER_NAME,
    )
    checks = {
        "one_shot_claim_bound": True,
        "sealed_case_count": result["counts"]["case_count"] == EXPECTED_CASE_COUNT,
        "raw_relevant_count_zero": result["counts"]["raw_relevant_count"] == 0,
        "approved_count_zero": result["counts"]["approved_count"] == 0,
        "protocol_failure_batches_zero": result["counts"]["protocol_failure_batches"]
        == 0,
        "protocol_failure_cases_zero": result["counts"]["protocol_failure_cases"] == 0,
        "approved_upper_bound_at_most_0_05": result["metrics"][
            "approved_one_sided_exact_95_upper"
        ]
        <= 0.05,
        "exact_batch_call_count": result["usage"]["model_call_count"]
        == math.ceil(EXPECTED_CASE_COUNT / DEFAULT_BATCH_SIZE),
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "one_shot_claim": validated_claim,
        "production_authority": False,
        "holdout": holdout,
        "implementation_freeze": freeze,
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "verifier_route": qwen_verifier_route_identity(),
        **build_hybrid_decision_contract(verifier_model),
        "resolver_pressure": result,
        "acceptance_gate": {"passed": False, "checks": checks},
    }
    checks["content_free_report"] = _is_content_free(report, dataset)
    report["acceptance_gate"]["passed"] = all(checks.values())
    report["report_fingerprint"] = evidence_fingerprint(report)
    return report


def _post_claim_failure_kind(error: Exception, *, stage: str) -> str:
    if isinstance(error, ResolverProviderTransportFailure):
        return "provider_or_transport_error"
    if isinstance(error, (TypeError, ValueError, AssertionError)):
        return "post_claim_validation_error"
    del stage
    return "unexpected_error"


def build_post_claim_failure_report(
    *,
    holdout: dict[str, Any],
    freeze: dict[str, Any],
    one_shot_claim: dict[str, Any],
    failure_stage: str,
    failure_kind: str,
) -> dict[str, Any]:
    """Build the sole content-free artifact for a consumed failed run."""

    report = {
        "schema_version": POST_CLAIM_FAILURE_SCHEMA_VERSION,
        "protocol_version": HYBRID_PROTOCOL_VERSION,
        "synthetic_data": True,
        "one_shot": True,
        "production_authority": False,
        "one_shot_claim": validate_evaluation_one_shot_receipt(
            one_shot_claim,
            dataset_id=DATASET_ID,
            holdout_sha256=EXPECTED_HOLDOUT_SHA256,
            output_artifact=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
            marker_name=ONE_SHOT_MARKER_NAME,
        ),
        "holdout": dict(holdout),
        "implementation_freeze": freeze,
        "inference_profile": dict(HYBRID_PROMOTION_INFERENCE_PROFILE),
        "verifier_route": qwen_verifier_route_identity(),
        "failure_stage": failure_stage,
        "failure_kind": failure_kind,
        "acceptance_gate": {"passed": False},
    }
    report["report_fingerprint"] = evidence_fingerprint(report)
    return validate_post_claim_failure_report(report)


def validate_post_claim_failure_report(report: Any) -> dict[str, Any]:
    """Validate a spent-run receipt without accepting any free-form text."""

    if not isinstance(report, dict) or set(report) != _POST_CLAIM_FAILURE_KEYS:
        raise ValueError("resolver pressure failure report schema mismatch")
    fingerprint = report.get("report_fingerprint")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or fingerprint != evidence_fingerprint(report)
    ):
        raise ValueError("resolver pressure failure report fingerprint mismatch")
    expected_holdout = {
        "dataset_id": DATASET_ID,
        "holdout_sha256": EXPECTED_HOLDOUT_SHA256,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "case_count": EXPECTED_CASE_COUNT,
    }
    if (
        report.get("schema_version") != POST_CLAIM_FAILURE_SCHEMA_VERSION
        or report.get("protocol_version") != HYBRID_PROTOCOL_VERSION
        or report.get("synthetic_data") is not True
        or report.get("one_shot") is not True
        or report.get("production_authority") is not False
        or report.get("holdout") != expected_holdout
        or report.get("implementation_freeze") != implementation_freeze()
        or report.get("inference_profile")
        != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
        or report.get("verifier_route") != qwen_verifier_route_identity()
        or report.get("failure_stage") not in _POST_CLAIM_FAILURE_STAGES
        or report.get("failure_kind") not in _POST_CLAIM_FAILURE_KINDS
        or report.get("acceptance_gate") != {"passed": False}
    ):
        raise ValueError("resolver pressure failure report authority mismatch")
    validate_evaluation_one_shot_receipt(
        report.get("one_shot_claim"),
        dataset_id=DATASET_ID,
        holdout_sha256=EXPECTED_HOLDOUT_SHA256,
        output_artifact=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        marker_name=ONE_SHOT_MARKER_NAME,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verifier-model", default=DEFAULT_VERIFIER_MODEL)
    parser.add_argument("--acknowledge-one-shot", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_one_shot:
        raise SystemExit("resolver pressure evaluation requires --acknowledge-one-shot")
    exact_paths = {
        "holdout": (args.holdout, DEFAULT_HOLDOUT),
        "report output": (args.output, DEFAULT_OUTPUT),
    }
    for label, (actual, expected) in exact_paths.items():
        if actual.expanduser().resolve() != expected.resolve():
            raise SystemExit(f"{label} is fixed for the authoritative one-shot run")
    if args.verifier_model != DEFAULT_VERIFIER_MODEL:
        raise SystemExit("verifier model is fixed for the authoritative one-shot run")
    if args.output.exists():
        raise SystemExit(
            "resolver pressure report already exists; evaluation is one-shot"
        )

    # Validate every local, deterministic precondition before consuming the
    # cooperative one-shot marker. The sealed payload remains in-process only;
    # no provider exists yet and no case result is disclosed.
    freeze = implementation_freeze()
    dataset, holdout = load_frozen_holdout(args.holdout)

    from minicode.config import load_runtime_config
    from minicode.env_file import apply_env_file
    from minicode.model_registry import create_dedicated_model_adapter

    apply_env_file([args.env_file])
    runtime = build_hybrid_promotion_verifier_runtime(load_runtime_config(ROOT))
    canary_model = create_exact_subagent_verifier(
        runtime,
        args.verifier_model,
        adapter_factory=create_dedicated_model_adapter,
    )
    try:
        run_fixed_resolver_route_canary(canary_model)
    except ResolverRouteCanaryError as error:
        # This happens before the one-shot marker and output artifact exist.
        # Surface only the closed reason enum, never provider/model text.
        raise SystemExit(str(error)) from None
    model = create_exact_subagent_verifier(
        runtime,
        args.verifier_model,
        adapter_factory=create_dedicated_model_adapter,
    )
    # Claim immediately before the first remote inference. Structural, route,
    # credential-shape, output-path, and fixed route-canary failures above must
    # not burn a run.  The sealed run always receives a fresh adapter so the
    # canary cannot leak reasoning state or call-budget state into evaluation.
    one_shot_claim = asdict(
        claim_evaluation_run(
            DEFAULT_ONE_SHOT_MARKER,
            dataset_id=DATASET_ID,
            holdout_sha256=EXPECTED_HOLDOUT_SHA256,
            output_artifact=HYBRID_RESOLVER_PRESSURE_ARTIFACT,
        )
    )
    failure_stage = "evaluate_resolver"
    try:
        result = evaluate_resolver(dataset, model)
        failure_stage = "build_report"
        report = build_report(
            dataset=dataset,
            holdout=holdout,
            freeze=freeze,
            one_shot_claim=one_shot_claim,
            verifier_model=args.verifier_model,
            result=result,
        )
        # Re-parse every closed-schema, content-free invariant at the final
        # write boundary. The producer cannot trust its own builder shape.
        failure_stage = "validate_report"
        report = validate_resolver_pressure_report(report)
    except Exception as error:  # noqa: BLE001 - spent run must leave evidence
        failure_report = build_post_claim_failure_report(
            holdout=holdout,
            freeze=freeze,
            one_shot_claim=one_shot_claim,
            failure_stage=failure_stage,
            failure_kind=_post_claim_failure_kind(error, stage=failure_stage),
        )
        _write_json_exclusive(args.output, failure_report)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "acceptance_gate": {"passed": False},
                    "failure_stage": failure_report["failure_stage"],
                    "failure_kind": failure_report["failure_kind"],
                    "report_fingerprint": failure_report["report_fingerprint"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2) from None
    _write_json_exclusive(args.output, report)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "acceptance_gate": report["acceptance_gate"],
                "counts": result["counts"],
                "metrics": result["metrics"],
                "usage": result["usage"],
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
