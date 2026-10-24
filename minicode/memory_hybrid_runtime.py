"""Local dense candidate generation plus strict LLM relevance adjudication."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minicode.agent_budget import AgentBudgetExceeded, record_budgeted_model_call
from minicode.embeddings import (
    EmbeddingEncoder,
    OpenAICompatibleEmbeddingEncoder,
    create_openai_compatible_embedding_client,
)
from minicode.memory_hybrid import (
    HYBRID_ALLOWED_DECISIONS,
    HYBRID_ALLOWED_REASONS,
    HYBRID_CHALLENGER_ALLOWED_REASONS,
    HYBRID_CHALLENGER_MODE,
    HYBRID_CHALLENGER_PROMPT_VERSION,
    HYBRID_CHALLENGER_SYSTEM_PROMPT,
    HYBRID_CHALLENGER_VETO_REASONS,
    HYBRID_IRRELEVANT_REASONS,
    HYBRID_MODEL_LIST_LIMIT,
    HYBRID_MODEL_STRING_LIMIT,
    HYBRID_PROMOTION_INFERENCE_PROFILE,
    HYBRID_PROMPT_VERSION,
    HYBRID_PROTOCOL_VERSION,
    HYBRID_QUERY_GATE_VERSION,
    HYBRID_RELEVANT_REASONS,
    HYBRID_RESOLVER_MODE,
    HYBRID_RESOLVER_NON_OVERRIDABLE_VETO_REASONS,
    HYBRID_RESOLVER_PROMPT_VERSION,
    HYBRID_RESOLVER_SEGMENT_VERSION,
    HYBRID_RESOLVER_SPAN_COORDINATE_SYSTEM,
    HYBRID_RESOLVER_SUPPORT_KINDS,
    HYBRID_RESOLVER_SYSTEM_PROMPT,
    HYBRID_SYSTEM_PROMPT,
    HybridActivation,
    HybridAdjudication,
    HybridCandidateSignal,
    bounded_hybrid_identity_strings,
    bounded_hybrid_text,
)
from minicode.memory_hybrid_promotion_contract import (
    QWEN_PROMOTION_DENSE_TOP_K,
    QWEN_PROMOTION_MAX_CANDIDATES,
    QWEN_PROMOTION_MAX_MODEL_CALLS,
    QWEN_VERIFIER_BASE_URL,
    qwen_verifier_route_identity,
)
from minicode.memory_hot_store import MemoryHotStore, get_memory_hot_store
from minicode.memory_path_identity import (
    complete_identity_occurrence_spans,
    extract_complete_path_identities,
    normalize_path_identity,
    path_identity_basename,
    redact_complete_paths,
)
from minicode.model_call_control import (
    ModelCallDeadlineExceeded,
    call_model_next,
    checkpoint_model_call,
)
from minicode.pricing import (
    pricing_failure_event_payload,
    project_model_cost_event,
)
from minicode.run_events import (
    emit_event_safely,
    new_model_operation_id,
    project_model_duration_ms,
    project_model_usage,
)
from minicode.turn_cancellation import TurnCancellationRequested

MAX_CONTENT_CHARS = 4_000
MAX_QUERY_CHARS = 2_000
MAX_CLASSIFIER_CONTENT_CHARS = 2_000
MAX_MODEL_FILES = HYBRID_MODEL_LIST_LIMIT
MAX_MANIFEST_BYTES = 128_000
MAX_CANDIDATES = QWEN_PROMOTION_MAX_CANDIDATES
DEFAULT_DENSE_TOP_K = QWEN_PROMOTION_DENSE_TOP_K
DEFAULT_BATCH_SIZE = 20
MAX_CACHE_ENTRIES = 64
MAX_PROVIDER_CACHE_ENTRIES = 8
DEFAULT_MAX_MODEL_CALLS_PER_TASK = QWEN_PROMOTION_MAX_MODEL_CALLS
MAX_RESOLVER_SEGMENTS_PER_SIDE = 16
MAX_RESOLVER_CHARS_PER_PAIR = 12_000
MAX_RESOLVER_CHARS_PER_BATCH = 120_000


class HybridProviderInitializationError(RuntimeError):
    """Fail-closed, content-free reason for Hybrid provider startup failure."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason).strip()[:96] or "provider_initialization_failed"
        super().__init__(self.reason)


class HybridResponseProtocolError(ValueError):
    """A provider responded, but not with a trusted terminal text envelope."""


_PROVIDER_CACHE_LOCK = threading.RLock()
_PROVIDER_CACHE: OrderedDict[str, _HybridSharedState] = OrderedDict()
_PROVIDER_CACHE_SALT = os.urandom(32)

_GENERIC_QUERY_TERMS = frozenset(
    {
        "behavior",
        "behaviour",
        "change",
        "check",
        "code",
        "error",
        "failure",
        "fix",
        "handle",
        "investigate",
        "issue",
        "it",
        "problem",
        "recover",
        "recovery",
        "repair",
        "resolve",
        "something",
        "task",
        "that",
        "the",
        "thing",
        "this",
        "update",
    }
)
_GENERIC_CJK_QUERY_PARTS = (
    "修复",
    "处理",
    "检查",
    "解决",
    "这个",
    "这种",
    "那个",
    "该",
    "它",
    "问题",
    "行为",
    "错误",
    "故障",
    "恢复",
    "一下",
    "代码",
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized(value: Any, limit: int) -> str:
    return bounded_hybrid_text(value, limit)


def _bounded_projection_is_complete(value: Any, limit: int) -> bool:
    """Return whether model-visible normalization fits without truncation."""

    return len(bounded_hybrid_text(value, limit + 1)) <= limit


def _bounded_strings(
    values: Any,
    *,
    limit: int = HYBRID_MODEL_LIST_LIMIT,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result = [_normalized(item, HYBRID_MODEL_STRING_LIMIT) for item in values[:limit]]
    return tuple(dict.fromkeys(item for item in result if item))


def _ordered_lexical_candidate_ids(
    values: Sequence[str] | frozenset[str],
) -> tuple[str, ...]:
    """Preserve canonical lexical evidence order; stabilize legacy sets."""

    if isinstance(values, (set, frozenset)):
        candidates = sorted(values)
    elif isinstance(values, (list, tuple)):
        candidates = list(values)
    else:
        raise TypeError("lexical candidate IDs must be an ordered sequence")
    if any(not isinstance(item, str) or not item for item in candidates):
        raise ValueError("lexical candidate IDs must be non-empty strings")
    if len(candidates) != len(set(candidates)):
        raise ValueError("lexical candidate IDs must be unique")
    return tuple(candidates)


def _take_complete_candidate_lane(
    ordered_ids: Sequence[str],
    *,
    eligible: dict[str, Any],
    limit: int,
    excluded_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Fill one lane without letting incomplete model inputs consume slots."""

    accepted: list[str] = []
    rejected: list[str] = []
    seen = set(excluded_ids or ())
    for entry_id in ordered_ids:
        if len(accepted) >= limit:
            break
        if entry_id in seen or entry_id not in eligible:
            continue
        seen.add(entry_id)
        if not _bounded_projection_is_complete(
            getattr(eligible[entry_id], "content", ""),
            MAX_CLASSIFIER_CONTENT_CHARS,
        ):
            rejected.append(entry_id)
            continue
        accepted.append(entry_id)
    return accepted, rejected


def _safe_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    result: dict[str, Any] = {}
    identity_keys = {
        "alias_from",
        "alias_to",
        "correction_id",
        "path",
        "paths",
        "files",
        "file_paths",
        "migration_id",
        "renamed_from",
        "renamed_to",
        "supersedes",
    }
    for key in (
        "alias_from",
        "alias_to",
        "path",
        "paths",
        "files",
        "file_paths",
        "relation",
        "component",
        "library",
        "version",
        "config_key",
        "value",
        "renamed_from",
        "renamed_to",
        "migration_id",
        "correction_id",
        "supersedes",
    ):
        value = metadata.get(key)
        if key in identity_keys:
            values = (value,) if isinstance(value, str) else value
            projected = bounded_hybrid_identity_strings(values)
            if not projected:
                continue
            result[key] = projected[0] if isinstance(value, str) else list(projected)
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = _normalized(value, 256)
        elif isinstance(value, (list, tuple)):
            result[key] = list(_bounded_strings(value))
    return result


def _entry_is_dense_eligible(entry: Any) -> bool:
    tier = getattr(entry, "tier", None)
    tier_value = getattr(tier, "value", tier)
    return (
        getattr(entry, "approval_status", "") == "approved"
        and getattr(entry, "lifecycle_status", "") == "active"
        and getattr(entry, "safety_status", "") == "safe"
        and not bool(getattr(entry, "curator_locked", False))
        and tier_value != "archival"
        and bool(_normalized(getattr(entry, "content", ""), MAX_CONTENT_CHARS))
    )


def _entry_document(entry: Any) -> str:
    fields = _canonical_entry_fields(entry, content_limit=MAX_CONTENT_CHARS)
    parts = [
        f"content: {fields['content']}",
        f"category: {fields['category']}",
    ]
    tags = fields["tags"]
    domains = fields["domains"]
    metadata = fields["metadata"]
    if tags:
        parts.append(f"tags: {' | '.join(tags)}")
    if domains:
        parts.append(f"domains: {' | '.join(domains)}")
    if metadata:
        parts.append(f"metadata: {_stable_json(metadata)}")
    return "\n".join(parts)


def _canonical_entry_fields(
    entry: Any,
    *,
    content_limit: int = MAX_CLASSIFIER_CONTENT_CHARS,
) -> dict[str, Any]:
    """Return the structured, bounded entry projection seen by semantic stages."""

    scope = getattr(entry, "scope", None)
    return {
        "content": _normalized(getattr(entry, "content", ""), content_limit),
        "scope": getattr(scope, "value", scope),
        "category": _normalized(getattr(entry, "category", ""), 128),
        # Tags and domains can carry identifiers. Drop an overlong value
        # instead of fabricating an apparently exact prefix.
        "tags": list(
            bounded_hybrid_identity_strings(getattr(entry, "tags", ()))
        ),
        "domains": list(
            bounded_hybrid_identity_strings(getattr(entry, "domains", ()))
        ),
        "metadata": _safe_metadata(getattr(entry, "metadata", {})),
    }


def _canonical_request_fields(request: Any) -> dict[str, Any]:
    """Return the sole bounded request projection used by every semantic stage."""

    return {
        "query": _normalized(getattr(request, "query", ""), MAX_QUERY_CHARS),
        "current_files": list(
            bounded_hybrid_identity_strings(
                getattr(request, "current_files", ())
            )
        ),
        "active_domains": list(
            bounded_hybrid_identity_strings(
                getattr(request, "active_domains", ()),
                lowercase=True,
            )
        ),
    }


def _query_document(request: Any) -> str:
    fields = _canonical_request_fields(request)
    parts = [f"task: {fields['query']}"]
    files = fields["current_files"]
    domains = fields["active_domains"]
    if files:
        parts.append(f"current_files: {' | '.join(files)}")
    if domains:
        parts.append(f"active_domains: {' | '.join(domains)}")
    return "\n".join(parts)


_QUERY_SEGMENT_KINDS = {
    "task": "query.task",
    "current_files": "query.current_files",
    "active_domains": "query.active_domains",
}
_MEMORY_SEGMENT_KINDS = {
    "content": "memory.content",
    "category": "memory.category",
    "tags": "memory.tags",
    "domains": "memory.domains",
    "metadata": "memory.metadata",
}


def _proof_segments(
    document: str,
    prefix: str,
    *,
    side: str,
) -> tuple[dict[str, Any], ...]:
    """Create bounded exact-source segments that a resolver can cite by ID."""

    kind_map = (
        _QUERY_SEGMENT_KINDS
        if side == "query"
        else _MEMORY_SEGMENT_KINDS
        if side == "memory"
        else None
    )
    if kind_map is None:
        raise ValueError("hybrid resolver proof segment side is invalid")
    segments: list[dict[str, Any]] = []
    cursor = 0
    for raw_line in document.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        line_start = cursor
        cursor += len(raw_line)
        label = line.partition(":")[0].strip()
        kind = kind_map.get(label)
        if kind is None:
            raise ValueError("hybrid resolver proof segment source is invalid")
        for offset in range(0, len(line), 480):
            fragment = line[offset : offset + 480].strip()
            if not fragment or not any(character.isalnum() for character in fragment):
                continue
            start = document.find(fragment, line_start + offset, line_start + len(line))
            if start < 0:
                raise ValueError("hybrid resolver segment projection failed")
            segments.append(
                {
                    "id": f"{prefix}{len(segments) + 1}",
                    "kind": kind,
                    "text": fragment,
                    "start": start,
                    "end": start + len(fragment),
                }
            )
    if not segments or len(segments) > MAX_RESOLVER_SEGMENTS_PER_SIDE:
        raise ValueError("hybrid resolver document has no proof segments")
    return tuple(segments)


def _resolver_pair_prefix(entry_id: str) -> str:
    digest = _sha256_bytes(entry_id.encode("utf-8"))[:20]
    return f"p-{digest}-"


def _build_resolver_batch(
    items: Sequence[tuple[Any, Any]],
    *,
    decisions: dict[str, dict[str, Any]],
    audits: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build the sole production/evaluator resolver payload contract."""

    entry_ids = [str(entry.id) for _request, entry in items]
    if (
        not items
        or len(entry_ids) > DEFAULT_BATCH_SIZE
        or len(entry_ids) != len(set(entry_ids))
        or set(entry_ids) != set(decisions)
        or set(entry_ids) != set(audits)
    ):
        raise ValueError("hybrid resolver batch identity is invalid")
    pairs: list[dict[str, Any]] = []
    expected: dict[str, dict[str, Any]] = {}
    all_segment_ids: set[str] = set()
    for request, entry in items:
        audit = audits[entry.id]
        if (
            audit.get("admit") is not False
            or audit.get("reasonCode") not in HYBRID_CHALLENGER_VETO_REASONS
            or audit.get("reasonCode") in HYBRID_RESOLVER_NON_OVERRIDABLE_VETO_REASONS
        ):
            raise ValueError("hybrid resolver received an ineligible disagreement")
        pair_prefix = _resolver_pair_prefix(str(entry.id))
        query_segments = _proof_segments(
            _query_document(request), f"{pair_prefix}q", side="query"
        )
        memory_segments = _proof_segments(
            _entry_document(entry), f"{pair_prefix}m", side="memory"
        )
        segment_ids = {segment["id"] for segment in (*query_segments, *memory_segments)}
        if len(segment_ids) != len(query_segments) + len(memory_segments):
            raise ValueError("hybrid resolver pair has duplicate segment IDs")
        if all_segment_ids.intersection(segment_ids):
            raise ValueError("hybrid resolver batch has cross-pair segment collision")
        all_segment_ids.update(segment_ids)
        pair = {
            "id": entry.id,
            "queryEvidenceSegments": [
                {
                    "id": segment["id"],
                    "kind": segment["kind"],
                    "text": segment["text"],
                }
                for segment in query_segments
            ],
            "memoryEvidenceSegments": [
                {
                    "id": segment["id"],
                    "kind": segment["kind"],
                    "text": segment["text"],
                }
                for segment in memory_segments
            ],
            "verifiedRecoveryAuthority": _entry_has_verified_recovery(entry),
            "verifiedCauseAuthority": _entry_has_verified_cause(entry),
            "primary": {
                key: decisions[entry.id][key]
                for key in (
                    "decision",
                    "confidence",
                    "objectMatch",
                    "relationSupported",
                    "reasonCode",
                )
            },
            "challenger": {
                key: audits[entry.id][key]
                for key in ("admit", "confidence", "reasonCode")
            },
        }
        if len(_stable_json(pair)) > MAX_RESOLVER_CHARS_PER_PAIR:
            raise ValueError("hybrid resolver pair exceeds the proof input budget")
        pairs.append(pair)
        expected[entry.id] = {
            "query_segments": {
                segment["id"]: {
                    "start": segment["start"],
                    "end": segment["end"],
                    "kind": segment["kind"],
                }
                for segment in query_segments
            },
            "memory_segments": {
                segment["id"]: {
                    "start": segment["start"],
                    "end": segment["end"],
                    "kind": segment["kind"],
                }
                for segment in memory_segments
            },
            "contested_reason": audits[entry.id]["reasonCode"],
        }
    if len(_stable_json({"pairs": pairs})) > MAX_RESOLVER_CHARS_PER_BATCH:
        raise ValueError("hybrid resolver batch exceeds the proof input budget")
    return pairs, expected


def _resolver_input_rejection(
    entry_id: str,
    *,
    contested_reason: str,
) -> dict[str, Any]:
    """Return a local deny result when one proof projection is unrepresentable."""

    return {
        "id": entry_id,
        "decision": "irrelevant",
        "confidence": 1.0,
        "queryGrounded": False,
        "objectMatch": False,
        "relationSupported": False,
        "supportKind": "none",
        "disqualifier": "unsupported_relation",
        "contestedReason": contested_reason,
        "querySpan": {},
        "memorySpan": {},
        "querySegmentKind": "",
        "memorySegmentKind": "",
        "runtimeReason": "resolver_input_budget_rejected",
    }


def _query_has_concrete_object(request: Any) -> bool:
    if bounded_hybrid_identity_strings(getattr(request, "current_files", ())):
        return True
    text = _normalized(getattr(request, "query", ""), MAX_QUERY_CHARS).lower()
    if not text:
        return False
    if re.search(r"(?:[/\\]|\b[a-z0-9_-]+\.[a-z0-9]{1,12}\b)", text):
        return True
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9_-]*", text)
    informative_ascii_terms = {
        term for term in ascii_terms if term not in _GENERIC_QUERY_TERMS
    }
    # A single bare noun ("fix the cache") is not enough authority to send
    # stored guidance to a semantic model.  Require either explicit file
    # context or two independently useful object/operation terms.
    if len(informative_ascii_terms) >= 2:
        return True
    non_ascii = "".join(character for character in text if ord(character) > 127)
    for generic in _GENERIC_CJK_QUERY_PARTS:
        non_ascii = non_ascii.replace(generic, "")
    return (
        sum(character.isalpha() or character.isdigit() for character in non_ascii) >= 2
    )


_REMOVED_MARKERS = (
    "removed",
    "deprecated",
    "no longer supported",
    "unsupported since",
    "已移除",
    "已删除",
    "不再支持",
    "废弃",
)
_HISTORICAL_CONFIGURATION_QUERY_MARKERS = (
    "why",
    "explain",
    "explanation",
    "rationale",
    "reason for",
    "history",
    "historical",
    "removal",
    "removed",
    "retire",
    "retired",
    "retiring",
    "retirement",
    "sunset",
    "decommission",
    "deprecated",
    "deprecation",
    "migration",
    "migrate",
    "compatibility",
    "used to",
    "what changed",
    "when was",
    "为什么",
    "为何",
    "原因",
    "缘由",
    "历史",
    "移除",
    "删除",
    "下线",
    "淘汰",
    "废弃",
    "迁移",
    "兼容",
    "曾经",
    "何时",
)
_CURRENT_CONFIGURATION_QUERY_MARKERS = (
    "use",
    "using",
    "configure",
    "enable",
    "apply",
    "add",
    "restore",
    "keep",
    "enforce",
    "adopt",
    "implement",
    "install",
    "turn on",
    "current",
    "latest",
    "使用",
    "配置",
    "设置",
    "启用",
    "应用",
    "添加",
    "恢复",
    "保留",
    "执行",
    "采用",
    "安装",
    "开启",
    "当前",
    "现在",
    "最新",
)


def _normalized_path(value: Any) -> str:
    projected = bounded_hybrid_identity_strings((value,))
    if not projected:
        return ""
    # Preserve path case.  The retrieval runtime does not own a trustworthy
    # workspace-filesystem case-sensitivity signal, so folding here could turn
    # two distinct paths on a case-sensitive checkout into deterministic proof
    # of identity (or of a migration).  Uncertain case-only matches remain
    # available to semantic adjudication; they must not bypass it.
    return normalize_path_identity(projected[0])


def _paths_in_text(value: Any) -> set[str]:
    return set(extract_complete_path_identities(value, max_text_chars=4_000))


def _metadata_paths(metadata: dict[str, Any], *keys: str) -> set[str]:
    paths: set[str] = set()
    for key in keys:
        value = metadata.get(key)
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            if isinstance(item, str) and (normalized := _normalized_path(item)):
                paths.add(normalized)
    return paths


def _query_paths(request: Any) -> set[str]:
    fields = _canonical_request_fields(request)
    return {
        *(
            _normalized_path(path)
            for path in fields["current_files"]
            if _normalized_path(path)
        ),
        *_paths_in_text(fields["query"]),
    }


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _contains_intent_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Match ASCII intent words on token boundaries and CJK markers literally."""

    for marker in markers:
        if marker.isascii():
            if re.search(
                rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                text,
            ):
                return True
        elif marker in text:
            return True
    return False


def _query_has_current_configuration_intent(query: str) -> bool:
    """Require unambiguous current/imperative evidence before stale vetoes."""

    if _contains_intent_marker(query, _CURRENT_CONFIGURATION_QUERY_MARKERS):
        return True
    # `set` is tense-ambiguous ("who set it" is historical). It is current
    # authority only in an imperative or modal construction.
    return bool(
        re.search(
            r"(?:^|[.!?]\s*)(?:please\s+)?set\b|"
            r"\b(?:must|should|need\s+to|want\s+to|can\s+you|could\s+you)\s+set\b",
            query,
        )
    )


def _query_has_historical_configuration_intent(query: str) -> bool:
    """Recognize explanatory and past-tense configuration requests."""

    if _contains_intent_marker(
        query,
        _HISTORICAL_CONFIGURATION_QUERY_MARKERS,
    ):
        return True
    if re.search(
        r"\b(?:audit|trace|investigate)?\s*(?:who|when)\b.{0,120}"
        r"\b(?:set|configured|enabled|installed|removed)\b",
        query,
    ):
        return True
    cjk_verbs = r"(?:设置|配置|启用|应用|添加|恢复|采用|安装|开启)"
    return bool(
        re.search(
            rf"(?:审计|追溯|调查|谁|是谁|何时|什么时候|当时|曾经)"
            rf".{{0,160}}{cjk_verbs}",
            query,
        )
        or re.search(rf"{cjk_verbs}(?:过|了)", query)
    )


def _content_has_post_removal_guidance(content: str) -> bool:
    """Detect affirmative replacement guidance on either side of removal."""

    if not _contains_any(content, _REMOVED_MARKERS):
        return False
    clauses = tuple(
        clause.strip()
        for clause in re.split(r"[.;!?。；！？\r\n]+", content)
        if clause.strip()
    )
    positive_pattern = re.compile(
        r"\b(?:use|using|configure|set|enable|adopt|replace(?:d|ment)?)\b"
    )
    negative_pattern = re.compile(
        r"\b(?:do\s+not|don't|never|avoid|without|instead\s+of|used\s+to|"
        r"was\s+using|if|might|could)\b"
    )
    for clause in clauses:
        has_positive = bool(positive_pattern.search(clause)) or _contains_any(
            clause,
            ("改用", "使用", "配置", "设置", "启用", "替代", "取代"),
        )
        if not has_positive:
            continue
        has_negative = bool(negative_pattern.search(clause)) or _contains_any(
            clause,
            (
                "不要",
                "不得",
                "禁止",
                "不再",
                "未",
                "曾经",
                "过去",
                "如果",
                "可能",
            ),
        )
        if not has_negative:
            return True
    return False


_POLICY_ANCHOR_STOPWORDS = _GENERIC_QUERY_TERMS | {
    "after",
    "allow",
    "allowed",
    "before",
    "deny",
    "disable",
    "disabled",
    "do",
    "enable",
    "enabled",
    "forbid",
    "keep",
    "must",
    "never",
    "not",
    "only",
    "require",
    "requires",
    "should",
    "without",
    "at",
    "for",
    "from",
    "in",
    "of",
    "on",
    "to",
}
_POLICY_GENERIC_OBJECT_ANCHORS = {
    "access",
    "admin",
    "app",
    "application",
    "component",
    "endpoint",
    "interface",
    "mode",
    "option",
    "permission",
    "policy",
    "rule",
    "service",
    "setting",
    "system",
    "user",
    "users",
    "worker",
}


def _policy_anchor_terms(value: str) -> set[str]:
    folded = value.casefold()
    for generic in _GENERIC_CJK_QUERY_PARTS:
        folded = folded.replace(generic, " ")
    anchors = {
        term
        for term in re.findall(r"[a-z0-9][a-z0-9_-]*", folded)
        if len(term) >= 3 and term not in _POLICY_ANCHOR_STOPWORDS
    }
    for span in re.findall(r"[\u3400-\u9fff]+", folded):
        anchors.update(span[index : index + 2] for index in range(len(span) - 1))
    return anchors


_POLICY_CLAUSE_SPLIT_RE = re.compile(
    r"(?:[.;!?。；！？，,\n]+|\b(?:then|while|whereas|but|and)\b|然后|同时|而且|但是|但)",
    re.IGNORECASE,
)


def _policy_clauses(value: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in _POLICY_CLAUSE_SPLIT_RE.split(value.casefold())
        if clause.strip()
    )


def _single_policy_predicate(
    value: str,
    *,
    positive: Callable[[str], bool],
    negative: Callable[[str], bool],
) -> tuple[bool, str] | None:
    """Return one unambiguous marker-bearing clause, otherwise defer."""

    classified: list[tuple[bool, str]] = []
    for clause in _policy_clauses(value):
        has_positive = positive(clause)
        has_negative = negative(clause)
        if has_positive and has_negative:
            return None
        if has_positive or has_negative:
            classified.append((has_positive, clause))
    if len(classified) != 1:
        return None
    return classified[0]


def _opposed_policy_predicates(
    query: str,
    memory: str,
    *,
    positive: Callable[[str], bool],
    negative: Callable[[str], bool],
    marker_literals: tuple[str, ...],
    strict_object_binding: bool = False,
) -> bool:
    """Prove a conflict only for one same-object predicate on each side."""

    query_predicate = _single_policy_predicate(
        query,
        positive=positive,
        negative=negative,
    )
    memory_predicate = _single_policy_predicate(
        memory,
        positive=positive,
        negative=negative,
    )
    if (
        query_predicate is None
        or memory_predicate is None
        or query_predicate[0] == memory_predicate[0]
    ):
        return False

    def anchors(clause: str) -> set[str]:
        for marker in sorted(marker_literals, key=len, reverse=True):
            clause = clause.replace(marker, " ")
        # File identity is evaluated by its own deterministic gate.  Path
        # components must not dilute or manufacture predicate-object overlap.
        return _policy_anchor_terms(redact_complete_paths(clause))

    def predicate_tail(clause: str) -> str:
        marker_ends: list[int] = []
        for marker in marker_literals:
            if marker.isascii():
                match = re.search(
                    rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                    clause,
                )
                if match is not None:
                    marker_ends.append(match.end())
            else:
                index = clause.find(marker)
                if index >= 0:
                    marker_ends.append(index + len(marker))
        if not marker_ends:
            return ""
        return redact_complete_paths(clause[min(marker_ends) :]).strip()

    if strict_object_binding:
        query_tail = predicate_tail(query_predicate[1])
        memory_tail = predicate_tail(memory_predicate[1])
        query_objects = (
            _policy_anchor_terms(query_tail) - _POLICY_GENERIC_OBJECT_ANCHORS
        )
        memory_objects = (
            _policy_anchor_terms(memory_tail) - _POLICY_GENERIC_OBJECT_ANCHORS
        )
        if not query_objects or not memory_objects:
            return False
        if re.search(r"[\u3400-\u9fff]", query_tail + memory_tail):
            # CJK bigram counts cannot bind grammatical roles. Only identical
            # post-polarity predicates are deterministic proof; paraphrases
            # remain for semantic adjudication.
            query_cjk = "".join(re.findall(r"[\u3400-\u9fff]", query_tail))
            memory_cjk = "".join(re.findall(r"[\u3400-\u9fff]", memory_tail))
            return bool(query_cjk and query_cjk == memory_cjk)

        def first_object_term(tail: str) -> str:
            for term in re.findall(r"[a-z0-9][a-z0-9_-]*", tail):
                if (
                    len(term) >= 3
                    and term not in _POLICY_ANCHOR_STOPWORDS
                    and term not in _POLICY_GENERIC_OBJECT_ANCHORS
                ):
                    return term
            return ""

        if first_object_term(query_tail) != first_object_term(memory_tail):
            return False
        shared_objects = query_objects & memory_objects
        union_objects = query_objects | memory_objects
        # A deterministic polarity veto needs a near-equivalent
        # action-object predicate. A common topic noun is not proof.
        return bool(
            shared_objects
            and len(shared_objects) * 3 >= len(union_objects) * 2
        )

    query_anchors = anchors(query_predicate[1])
    memory_anchors = anchors(memory_predicate[1])
    shared_anchors = query_anchors & memory_anchors
    if len(shared_anchors) < 2:
        return False

    # For Latin predicates, require the marker-bearing clauses to describe
    # substantially the same object, not merely the same actor or container.
    # This prevents e.g. "enable read access for admin" from vetoing
    # "disable write access for admin".  CJK bigram anchors do not have a
    # comparable word-level denominator, so retain the conservative two-anchor
    # proof used for those clauses.
    query_ascii = {term for term in query_anchors if term.isascii()}
    memory_ascii = {term for term in memory_anchors if term.isascii()}
    if query_ascii and memory_ascii:
        shared_ascii = query_ascii & memory_ascii
        union_ascii = query_ascii | memory_ascii
        return len(shared_ascii) >= 2 and len(shared_ascii) * 3 >= len(union_ascii) * 2
    return True


def _query_has_affirmative_rename_intent(query: str) -> bool:
    """Admit only current, affirmative migration instructions."""

    folded = query.casefold()
    if re.search(
        r"\b(?:do\s+not|don't|never|avoid|without|if|whether|hypothetical|"
        r"suppose|supposing|might|could|would|what\s+if|explain|compare|"
        r"discuss|audit|why)\b",
        folded,
    ) or _contains_any(
        folded,
        (
            "不要",
            "不得",
            "禁止",
            "如果",
            "假如",
            "是否",
            "解释",
            "比较",
            "讨论",
            "审查",
            "此前",
            "曾经",
            "过去",
            "历史",
            "记录",
            "为何",
            "为什么",
        ),
    ):
        return False
    action = r"(?:rename|migrate|move)"
    if re.search(
        rf"(?:^|[;.!?]\s*)(?:please\s+)?{action}\b",
        folded,
    ):
        return True
    if re.search(
        rf"\b(?:apply|perform|execute|implement|complete|continue|follow|use)\b"
        rf".{{0,64}}\b{action}\b",
        folded,
    ):
        return True
    return bool(
        re.search(
            r"(?:^|[。；！？\n])\s*(?:请|现在|立即|继续)?\s*"
            r"(?:将|把|从|执行迁移|应用迁移|进行迁移|重命名|迁移|移动)",
            folded,
        )
    )


def _rename_claim_is_affirmative(value: str) -> bool:
    """Reject negated, failed, hypothetical, or merely planned migrations."""

    folded = value.casefold()
    if re.search(
        r"\b(?:not|never|didn't|doesn't|isn't|wasn't|hasn't|haven't|"
        r"fail(?:ed|ure)?|unsuccessful|abort(?:ed)?|cancel(?:led|ed)?|"
        r"rollback|rolled\s+back|revert(?:ed)?|attempt(?:ed)?|tr(?:y|ied)|"
        r"plan(?:ned)?|propos(?:e|ed)|hypothetical|might|could|would|"
        r"should)\b",
        folded,
    ):
        return False
    return not re.search(
        r"(?:尚未|并未|未曾|未能|没有|失败|不成功|中止|取消|回滚|撤销|"
        r"尝试|计划|拟将|预计|假设|可能|应该|应当|建议)",
        folded,
    )


def _text_proves_rename_direction(
    value: str,
    *,
    renamed_from: set[str],
    renamed_to: set[str],
    require_current_instruction: bool,
) -> bool:
    """Prove an affirmative source-to-destination claim from one text."""

    if len(renamed_from) != 1 or len(renamed_to) != 1:
        return False
    normalized = value.replace("\\", "/")
    if require_current_instruction and not _query_has_affirmative_rename_intent(
        normalized
    ):
        return False
    if not _rename_claim_is_affirmative(normalized):
        return False
    action = (
        r"(?:rename|migrate|move|迁移|重命名|移动)"
        if require_current_instruction
        else r"(?:renamed|migrated|moved)"
    )

    source = next(iter(renamed_from))
    destination = next(iter(renamed_to))
    source_spans = complete_identity_occurrence_spans(source, normalized)
    destination_spans = complete_identity_occurrence_spans(destination, normalized)
    if len(source_spans) != 1 or len(destination_spans) != 1:
        return False
    source_start, source_end = source_spans[0]
    destination_start, _destination_end = destination_spans[0]
    if source_end >= destination_start:
        return False

    before_source = normalized[:source_start]
    between_paths = normalized[source_end:destination_start]
    open_identity = r"[`'\"([{<]*"
    close_identity = r"[`'\")\]}>]*"
    flags = re.IGNORECASE

    # ``rename ... from OLD to NEW``. The exact, case-sensitive path spans are
    # proved first; regex is deliberately limited to the relation text around
    # those spans and therefore cannot accept OLD.bak or a case-only alias.
    action_from = re.search(
        rf"{action}[^.;:!?；：！？。\r\n]{{0,96}}?\bfrom\s+"
        rf"{open_identity}$",
        before_source,
        flags,
    )
    source_to_destination = re.fullmatch(
        rf"{close_identity}\s+\b(?:to|into)\b\s+{open_identity}",
        between_paths,
        flags,
    )
    if action_from and source_to_destination:
        return True

    # ``OLD was moved to NEW`` and the corresponding source-first form.
    if re.fullmatch(
        rf"{close_identity}\s+(?:(?:was|is|has\s+been)\s+)?{action}\s+"
        rf"(?:\b(?:to|into|as)\b|到|至|为)\s*{open_identity}",
        between_paths,
        flags,
    ):
        return True

    chinese_prefix = (
        r"(?:从|将|把)\s*" if require_current_instruction else r"(?:已经|已)\s*(?:从|将|把)\s*"
    )
    return bool(
        re.search(rf"{chinese_prefix}{open_identity}$", before_source)
        and re.fullmatch(
            rf"{close_identity}\s*(?:迁移|重命名|移动)\s*(?:到|至|为)\s*"
            rf"{open_identity}",
            between_paths,
        )
    )


def _query_proves_rename_direction(
    query: str,
    *,
    renamed_from: set[str],
    renamed_to: set[str],
) -> bool:
    """Recognize only an explicit source-to-destination migration statement."""

    return _text_proves_rename_direction(
        query,
        renamed_from=renamed_from,
        renamed_to=renamed_to,
        require_current_instruction=True,
    )


def _rename_claim_is_unqualified(value: str) -> bool:
    """Reject scoped or conditional text from the global rename fast path."""

    folded = value.casefold()
    if re.search(
        r"\b(?:if|unless|except|only|when|provided|temporarily|partially)\b|"
        r"\bsubject\s+to\b|\bdepending\s+on\b|\bfor\s+tenant\b|"
        r"\bin\s+(?:staging|production|development|test)\b",
        folded,
    ):
        return False
    return not _contains_any(
        folded,
        (
            "如果",
            "除非",
            "仅",
            "只在",
            "仅限",
            "租户",
            "环境",
            "预发",
            "生产",
            "开发",
            "测试",
            "临时",
            "部分",
        ),
    )


def _rename_fast_path_is_single_purpose(query: str, memory: str) -> bool:
    """Require unambiguous migration-only text before bypassing semantics."""

    extra_policy_markers = (
        "keep",
        "retain",
        "discard",
        "drop",
        "enable",
        "disable",
        "allow",
        "deny",
        "forbid",
        "before",
        "after",
        "confirm",
        "localhost",
        "public",
        "保留",
        "丢弃",
        "启用",
        "禁用",
        "允许",
        "禁止",
        "之前",
        "之后",
        "确认",
        "本机",
        "公开",
    )
    coordinators = re.compile(
        r"\b(?:and|but|then|while|whereas|also)\b|(?:并且|以及|然后|同时|但是)"
    )
    internal_separator = re.compile(r"[\r\n;:!?；：！？。]|[.,，](?=\s*\S)")
    for value in (query, memory):
        surface = redact_complete_paths(value.casefold()).strip()
        if _resolver_action_families(value) != {"migration"}:
            return False
        if not _rename_claim_is_unqualified(surface):
            return False
        if _contains_intent_marker(surface, extra_policy_markers):
            return False
        if coordinators.search(surface) or internal_separator.search(surface):
            return False
    return True


_ORDER_SCAFFOLD_TERMS = frozenset(
    {
        "happen",
        "occur",
        "run",
        "start",
        "upload",
    }
)


def _normalized_order_anchor(term: str) -> str:
    if term.endswith("ies") and len(term) > 4:
        return term[:-3] + "y"
    if term.endswith("ing") and len(term) > 5:
        stem = term[:-3]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        return stem
    if term.endswith("ed") and len(term) > 4:
        return term[:-2]
    if term.endswith("s") and len(term) > 3:
        return term[:-1]
    return term


def _order_operand_terms(value: str) -> frozenset[str]:
    terms = {
        _normalized_order_anchor(term)
        for term in _policy_anchor_terms(value)
        if term.isascii()
    }
    return frozenset(term for term in terms if term not in _ORDER_SCAFFOLD_TERMS)


def _single_english_order_edge(
    value: str,
) -> tuple[frozenset[str], frozenset[str]] | None:
    """Normalize one unambiguous English before/after clause to an edge."""

    marker_clauses = [
        clause
        for clause in _policy_clauses(value)
        if re.search(r"\b(?:before|after)\b", clause)
    ]
    if len(marker_clauses) != 1:
        return None
    clause = marker_clauses[0]
    matches = tuple(re.finditer(r"\b(before|after)\b", clause))
    if len(matches) != 1:
        return None
    marker = matches[0]
    left = _order_operand_terms(clause[: marker.start()])
    right = _order_operand_terms(clause[marker.end() :])
    if not left or not right:
        return None
    return (left, right) if marker.group(1) == "before" else (right, left)


def _explicit_binary_conflict(query: str, memory: str) -> str | None:
    """Return only high-precision, explicitly opposed policy predicates."""

    query_folded = query.casefold()
    memory_folded = memory.casefold()

    def before(clause: str) -> bool:
        return bool(re.search(r"\bbefore\b", clause)) or "之前" in clause

    def after(clause: str) -> bool:
        return bool(re.search(r"\bafter\b", clause)) or "之后" in clause

    english_order_pair = bool(
        re.search(r"\b(?:before|after)\b", query_folded)
        and re.search(r"\b(?:before|after)\b", memory_folded)
    )
    if english_order_pair:
        query_edge = _single_english_order_edge(query_folded)
        memory_edge = _single_english_order_edge(memory_folded)
        if (
            query_edge is not None
            and memory_edge is not None
            and query_edge == (memory_edge[1], memory_edge[0])
        ):
            return "operation_order_conflict"
    elif _opposed_policy_predicates(
        query_folded,
        memory_folded,
        positive=before,
        negative=after,
        marker_literals=("before", "after", "之前", "之后"),
    ):
        return "operation_order_conflict"

    no_keep_markers = (
        "do not keep",
        "don't keep",
        "never keep",
        "不要保留",
        "不保留",
        "禁止保留",
        "不得保留",
    )

    def no_keep(clause: str) -> bool:
        return _contains_any(clause, no_keep_markers)

    def keep(clause: str) -> bool:
        return (
            bool(re.search(r"\bkeep\b", clause)) or "保留" in clause
        ) and not no_keep(clause)

    if _opposed_policy_predicates(
        query_folded,
        memory_folded,
        positive=keep,
        negative=no_keep,
        marker_literals=("keep", "保留", *no_keep_markers),
    ):
        return "negation_conflict"

    allow_pattern = r"\b(?:enable|enabled|allow|allowed|permit|permitted)\b"
    deny_pattern = (
        r"\b(?:disable|disabled|deny|denied|forbid|prohibit)\b|"
        r"\b(?:do\s+not|don't|never|must\s+not)\s+"
        r"(?:enable|allow|permit)\b"
    )

    def denies(clause: str) -> bool:
        return bool(re.search(deny_pattern, clause)) or _contains_any(
            clause, ("禁止", "不允许", "不得")
        )

    def allows(clause: str) -> bool:
        return (
            bool(re.search(allow_pattern, clause)) or "允许" in clause
        ) and not denies(clause)

    if _opposed_policy_predicates(
        query_folded,
        memory_folded,
        positive=allows,
        negative=denies,
        marker_literals=(
            "enable",
            "enabled",
            "allow",
            "allowed",
            "permit",
            "permitted",
            "disable",
            "disabled",
            "deny",
            "denied",
            "forbid",
            "prohibit",
            "允许",
            "禁止",
            "不允许",
            "不得",
        ),
        strict_object_binding=True,
    ):
        return "action_polarity_conflict"

    local_markers = ("localhost", "loopback", "127.0.0.1", "本机", "仅本地")
    public_markers = (
        "all interfaces",
        "public interface",
        "0.0.0.0",
        "所有网络接口",
        "公开监听",
    )
    if _opposed_policy_predicates(
        query_folded,
        memory_folded,
        positive=lambda clause: _contains_any(clause, local_markers),
        negative=lambda clause: _contains_any(clause, public_markers),
        # Remove polarity terms but retain the shared listening/interface
        # predicate so short CJK policies still have bindable object evidence.
        marker_literals=(
            "localhost",
            "loopback",
            "127.0.0.1",
            "本机",
            "仅本地",
            "all interfaces",
            "public",
            "0.0.0.0",
            "所有网络接口",
            "公开",
        ),
    ):
        return "network_exposure_conflict"

    no_confirmation = (
        "without confirmation",
        "without asking",
        "no confirmation",
        "无需确认",
        "无需询问",
        "无需再次询问",
        "不必确认",
    )
    require_confirmation = (
        "require confirmation",
        "requires confirmation",
        "must confirm",
        "must ask",
        "必须确认",
        "必须询问",
        "必须逐项确认",
        "逐项确认",
    )
    if _opposed_policy_predicates(
        query_folded,
        memory_folded,
        positive=lambda clause: _contains_any(clause, no_confirmation),
        negative=lambda clause: _contains_any(clause, require_confirmation),
        marker_literals=(*no_confirmation, *require_confirmation),
    ):
        return "confirmation_policy_conflict"
    return None


def _deterministic_applicability_rejection(request: Any, entry: Any) -> str | None:
    """Reject only conflicts proven by approved, hash-bound entry evidence."""

    query = _normalized(getattr(request, "query", ""), MAX_QUERY_CHARS)
    content = _normalized(getattr(entry, "content", ""), MAX_CONTENT_CHARS)
    metadata = _safe_metadata(getattr(entry, "metadata", {}))

    requested_paths = _query_paths(request)
    direct_paths = _metadata_paths(metadata, "path", "paths", "files", "file_paths")
    if not direct_paths:
        direct_paths = _paths_in_text(content)
    renamed_to = _metadata_paths(metadata, "renamed_to")
    proven_rename = bool(requested_paths & renamed_to and metadata.get("migration_id"))
    if requested_paths and direct_paths and not proven_rename:
        same_basename = any(
            path_identity_basename(query_path) == path_identity_basename(memory_path)
            for query_path in requested_paths
            for memory_path in direct_paths
        )
        explicitly_unrelated = str(metadata.get("relation", "")).casefold() in {
            "none",
            "unrelated",
            "different",
        }
        if not requested_paths.intersection(direct_paths) and (
            same_basename or explicitly_unrelated
        ):
            return "path_conflict"

    version = str(metadata.get("version", "")).strip().casefold()
    content_folded = content.casefold()
    query_folded = query.casefold()
    historical_or_explanatory_query = _query_has_historical_configuration_intent(
        query_folded
    )
    current_configuration_query = _query_has_current_configuration_intent(
        query_folded
    )
    if (
        version
        and _contains_any(content_folded, _REMOVED_MARKERS)
        and current_configuration_query
        and not historical_or_explanatory_query
        and not _content_has_post_removal_guidance(content_folded)
    ):
        version_tokens = {version, f"v{version}"}
        if not any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", query_folded)
            for token in version_tokens
        ):
            return "obsolete_configuration"

    return _explicit_binary_conflict(query, content)


def _deterministic_applicability_acceptance(request: Any, entry: Any) -> str | None:
    """Accept only an explicit, migration-backed rename to the requested path."""

    metadata = _safe_metadata(getattr(entry, "metadata", {}))
    raw_renamed_from = metadata.get("renamed_from")
    raw_renamed_to = metadata.get("renamed_to")
    if not (
        isinstance(raw_renamed_from, str)
        and raw_renamed_from.strip()
        and isinstance(raw_renamed_to, str)
        and raw_renamed_to.strip()
    ):
        return None
    requested_paths = _query_paths(request)
    renamed_to = _metadata_paths(metadata, "renamed_to")
    migration_id = str(metadata.get("migration_id") or "").strip()
    if requested_paths & renamed_to and migration_id:
        query = _normalized(getattr(request, "query", ""), MAX_QUERY_CHARS)
        content = _normalized(getattr(entry, "content", ""), MAX_CONTENT_CHARS)
        renamed_from = _metadata_paths(metadata, "renamed_from")
        query_paths = _paths_in_text(query)
        content_paths = _paths_in_text(content)
        migration_id_is_bound = bool(
            re.search(
                rf"(?<![A-Za-z0-9_.-]){re.escape(migration_id)}"
                rf"(?![A-Za-z0-9_.-])",
                content,
                re.IGNORECASE,
            )
        )
        # Only an explicit query-side old -> new path pair can bypass semantic
        # adjudication. Generic shared nouns such as "handler" or "policy"
        # are never deterministic migration evidence.
        if (
            bool(query_paths & renamed_from)
            and bool(query_paths & renamed_to)
            and bool(content_paths & renamed_to)
            and migration_id_is_bound
            and _rename_fast_path_is_single_purpose(query, content)
            and _query_proves_rename_direction(
                query,
                renamed_from=renamed_from,
                renamed_to=renamed_to,
            )
            and _text_proves_rename_direction(
                content,
                renamed_from=renamed_from,
                renamed_to=renamed_to,
                require_current_instruction=False,
            )
        ):
            return "proven_rename"
    return None


def _entry_provenance(entry: Any) -> dict[str, Any]:
    provenance = getattr(entry, "provenance", None)
    return provenance if isinstance(provenance, dict) else {}


def _provenance_tokens(provenance: dict[str, Any], key: str) -> set[str]:
    value = provenance.get(key)
    if isinstance(value, (list, tuple)):
        return {str(item).strip().casefold() for item in value if str(item).strip()}
    if isinstance(value, str):
        return {
            token.strip().casefold()
            for token in value.split(",")
            if token.strip()
        }
    return set()


def _entry_has_verified_cause(entry: Any) -> bool:
    """Recognize provenance that verifies a root cause, not merely a fix."""

    provenance = _entry_provenance(entry)
    evidence_kind = str(provenance.get("evidence_kind") or "").casefold()
    claim_type = str(provenance.get("claim_type") or "").casefold()
    if claim_type and claim_type != "root_cause":
        return False
    if evidence_kind:
        return evidence_kind == "verified_root_cause"
    evidence_tokens = _provenance_tokens(
        provenance,
        "durable_signals",
    ) | _provenance_tokens(provenance, "approval_basis")
    return bool(
        claim_type == "root_cause"
        and "confirmed_error_recovery_verified" in evidence_tokens
    )


def _entry_has_verified_recovery(entry: Any) -> bool:
    """Recognize provenance that verifies a recovery solution, not its cause."""

    provenance = _entry_provenance(entry)
    evidence_kind = str(provenance.get("evidence_kind") or "").casefold()
    claim_type = str(provenance.get("claim_type") or "").casefold()
    if claim_type and claim_type != "recovery":
        return False
    if evidence_kind:
        return evidence_kind == "verified_recovery"
    evidence_tokens = _provenance_tokens(
        provenance,
        "durable_signals",
    ) | _provenance_tokens(provenance, "approval_basis")
    # Historical single-entry reflections copied run-global signals into every
    # claim.  ``verified_solution`` alone can therefore belong to a sibling
    # approach or decision and is not typed recovery authority.  A canonical
    # claim_type (or the explicit evidence_kind above) is required.
    return bool(claim_type == "recovery" and "verified_solution" in evidence_tokens)


def _semantic_decision_has_required_authority(
    decision: dict[str, Any], entry: Any
) -> bool:
    """Enforce authority facts that an LLM vote cannot manufacture."""

    if decision.get("reasonCode") == "cause":
        return _entry_has_verified_cause(entry)
    if decision.get("reasonCode") == "recovery":
        return _entry_has_verified_recovery(entry)
    return True


def _entry_paths(entry: Any) -> set[str]:
    metadata = _safe_metadata(getattr(entry, "metadata", {}))
    return {
        *_paths_in_text(getattr(entry, "content", "")),
        *_metadata_paths(
            metadata,
            "path",
            "paths",
            "files",
            "file_paths",
            "renamed_from",
            "renamed_to",
        ),
    }


def _entry_has_established_preference(entry: Any) -> bool:
    category = str(getattr(entry, "category", "")).casefold()
    metadata = getattr(entry, "metadata", {})
    provenance = getattr(entry, "provenance", {})
    metadata_markers = {
        str(metadata.get(key) or "").strip().casefold()
        for key in ("relation", "claim_type", "kind")
    } if isinstance(metadata, dict) else set()
    evidence_kind = (
        str(provenance.get("evidence_kind") or "").casefold()
        if isinstance(provenance, dict)
        else ""
    )
    return (
        category in {"preference", "user_preference"}
        or bool(
            metadata_markers
            & {"preference", "user_preference", "explicit_preference"}
        )
        or evidence_kind in {"user_correction", "user_explicit_preference"}
    )


_RESOLVER_GENERIC_OPERATION_ANCHORS = {
    "component",
    "content",
    "config",
    "configuration",
    "file",
    "handler",
    "module",
    "note",
    "policy",
    "rule",
    "service",
    "setting",
    "组件",
    "配置",
    "文件",
    "模块",
    "服务",
    "规则",
    "策略",
    "说明",
}

_RESOLVER_ACTION_FAMILIES: dict[str, tuple[str, ...]] = {
    "presentation": (
        "annotate",
        "annotated",
        "format",
        "formatted",
        "formatting",
        "reformat",
        "style",
        "lint",
        "格式化",
        "排版",
        "美化",
        "标注",
    ),
    "computation": (
        "calculate",
        "calculated",
        "compute",
        "computed",
        "checksum",
        "hash",
        "计算",
        "校验和",
        "哈希",
    ),
    "configuration": (
        "configure",
        "configured",
        "set",
        "配置",
        "设置",
    ),
    "configuration_enable": (
        "enable",
        "enabled",
        "enables",
        "activate",
        "activated",
        "activates",
        "启用",
        "开启",
    ),
    "configuration_disable": (
        "disable",
        "disabled",
        "disables",
        "deactivate",
        "deactivated",
        "deactivates",
        "禁用",
        "关闭",
    ),
    "increase": (
        "increase",
        "increased",
        "increases",
        "raise",
        "raised",
        "extend",
        "extended",
        "增加",
        "提高",
        "延长",
    ),
    "decrease": (
        "decrease",
        "decreased",
        "decreases",
        "reduce",
        "reduced",
        "lower",
        "lowered",
        "shorten",
        "shortened",
        "减少",
        "降低",
        "缩短",
    ),
    "migration": (
        "rename",
        "renamed",
        "move",
        "moved",
        "migrate",
        "migrated",
        "迁移",
        "重命名",
        "移动",
    ),
    "removal": (
        "delete",
        "deleted",
        "remove",
        "removed",
        "purge",
        "删除",
        "移除",
        "清理",
    ),
    "execution": (
        "run",
        "runs",
        "ran",
        "execute",
        "executes",
        "executed",
        "运行",
        "执行",
    ),
    "execution_start": (
        "start",
        "starts",
        "started",
        "begin",
        "begins",
        "began",
        "启动",
        "开始",
    ),
    "execution_stop": (
        "stop",
        "stops",
        "stopped",
        "停止",
        "终止",
    ),
    "execution_restart": (
        "restart",
        "restarts",
        "restarted",
        "重启",
    ),
    "persistence": (
        "write",
        "save",
        "persist",
        "store",
        "写入",
        "保存",
        "持久化",
        "存储",
    ),
    "inspection": (
        "read",
        "load",
        "fetch",
        "inspect",
        "review",
        "读取",
        "加载",
        "获取",
        "检查",
    ),
}

_RESOLVER_ANCHOR_EQUIVALENTS = {
    "comment": "documentation",
    "comments": "documentation",
    "deadline": "timing",
    "timestamp": "timing",
    "timeout": "timing",
    "timeouts": "timing",
    "window": "timing",
}

_RESOLVER_OPPOSED_ACTION_FAMILIES = (
    ("configuration_enable", "configuration_disable"),
    ("execution_start", "execution_stop"),
    ("increase", "decrease"),
)


def _resolver_action_families(value: str) -> set[str]:
    """Extract explicit action families; generic verbs intentionally abstain."""

    folded = redact_complete_paths(value.casefold())
    families: set[str] = set()
    for family, markers in _RESOLVER_ACTION_FAMILIES.items():
        if _contains_intent_marker(folded, markers):
            families.add(family)
    if (
        re.search(r"\bretr(?:y|ies)\b", folded)
        and re.search(r"\b(?:timeout|timeouts|timestamp|window|deadline)\b", folded)
        and re.search(
            r"\b(?:fix|repair|resolve|start|starts|begin|begins|anchor|anchors)\b",
            folded,
        )
    ):
        families.add("retry_timing")
    return families


def _resolver_action_families_conflict(
    query_families: set[str],
    memory_families: set[str],
) -> bool:
    """Give any explicit direction conflict precedence over shared actions."""

    return any(
        (
            positive in query_families
            and negative in memory_families
        )
        or (
            negative in query_families
            and positive in memory_families
        )
        for positive, negative in _RESOLVER_OPPOSED_ACTION_FAMILIES
    )


def _resolver_operation_anchors(value: str) -> set[str]:
    """Return non-path, non-generic task anchors for resolver authorization."""

    without_paths = redact_complete_paths(value.casefold())
    anchors = _policy_anchor_terms(without_paths) - _RESOLVER_GENERIC_OPERATION_ANCHORS
    return {_RESOLVER_ANCHOR_EQUIVALENTS.get(anchor, anchor) for anchor in anchors}


def _resolver_evidence_fragments(
    resolution: dict[str, Any],
    *,
    entry: Any,
    request: Any,
) -> tuple[str, str] | None:
    query_document = _query_document(request)
    memory_document = _entry_document(entry)
    query_span = resolution.get("querySpan", {})
    memory_span = resolution.get("memorySpan", {})
    try:
        query_start = int(query_span["start"])
        query_end = int(query_span["end"])
        memory_start = int(memory_span["start"])
        memory_end = int(memory_span["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        query_start < 0
        or query_end <= query_start
        or query_end > len(query_document)
        or memory_start < 0
        or memory_end <= memory_start
        or memory_end > len(memory_document)
    ):
        return None
    return (
        query_document[query_start:query_end],
        memory_document[memory_start:memory_end],
    )


_RECOVERY_QUERY_MARKERS = (
    "error",
    "fail",
    "failed",
    "failure",
    "crash",
    "crashed",
    "hang",
    "hung",
    "stuck",
    "timeout",
    "remains alive",
    "does not exit",
    "won't exit",
    "recover",
    "recovery",
    "错误",
    "失败",
    "崩溃",
    "卡住",
    "超时",
    "未退出",
    "仍在运行",
    "恢复",
)
_RECOVERY_MEMORY_MARKERS = (
    "fix",
    "fixed",
    "repair",
    "resolve",
    "resolved",
    "recover",
    "recovery",
    "retry",
    "fallback",
    "reset",
    "shutdown",
    "shut down",
    "shuts down",
    "close",
    "join",
    "invoke",
    "instead",
    "修复",
    "解决",
    "恢复",
    "重试",
    "回退",
    "重置",
    "关闭",
    "替代",
)
_CONSTRAINT_MARKERS = (
    "must",
    "must not",
    "required",
    "requires",
    "never",
    "only",
    "do not",
    "don't",
    "禁止",
    "必须",
    "不得",
    "仅",
    "只允许",
)
_CONFIGURATION_ACTION_FAMILIES = frozenset(
    {"configuration", "configuration_enable", "configuration_disable"}
)
_RECOVERY_GENERIC_ANCHORS = {
    "crash",
    "crashed",
    "error",
    "fail",
    "failed",
    "failure",
    "fix",
    "fixed",
    "recover",
    "recovery",
    "repair",
    "resolve",
    "resolved",
    "retry",
    "timeout",
}


def _resolver_recovery_relation_is_bound(
    query_fragment: str,
    memory_fragment: str,
) -> bool:
    """Require recovery-shaped evidence bound to a shared concrete object."""
    shared_objects = (
        _resolver_operation_anchors(query_fragment)
        & _resolver_operation_anchors(memory_fragment)
    ) - _RECOVERY_GENERIC_ANCHORS
    return bool(
        _contains_intent_marker(query_fragment.casefold(), _RECOVERY_QUERY_MARKERS)
        and _contains_intent_marker(
            memory_fragment.casefold(),
            _RECOVERY_MEMORY_MARKERS,
        )
        and shared_objects
    )


def _resolver_constraint_relation_is_bound(
    query_fragment: str,
    memory_fragment: str,
) -> bool:
    query_folded = query_fragment.casefold()
    memory_folded = memory_fragment.casefold()
    return bool(
        _contains_intent_marker(query_folded, _CONSTRAINT_MARKERS)
        and _contains_intent_marker(memory_folded, _CONSTRAINT_MARKERS)
        and _resolver_semantic_relation_is_bound(
            query_fragment,
            memory_fragment,
        )
        and (
            _resolver_operation_anchors(query_fragment)
            & _resolver_operation_anchors(memory_fragment)
        )
    )


def _resolver_configuration_relation_is_bound(
    query_fragment: str,
    memory_fragment: str,
) -> bool:
    query_actions = _resolver_action_families(query_fragment)
    memory_actions = _resolver_action_families(memory_fragment)
    return bool(
        query_actions.intersection(_CONFIGURATION_ACTION_FAMILIES)
        and memory_actions.intersection(_CONFIGURATION_ACTION_FAMILIES)
        and query_actions.intersection(memory_actions)
        and not _resolver_action_families_conflict(query_actions, memory_actions)
        and _resolver_semantic_relation_is_bound(
            query_fragment,
            memory_fragment,
        )
        and (
            _resolver_operation_anchors(query_fragment)
            & _resolver_operation_anchors(memory_fragment)
        )
    )


def _resolver_semantic_relation_is_bound(
    query_fragment: str,
    memory_fragment: str,
) -> bool:
    """Bind cited clauses by polarity, action family, and specific objects."""

    query_payload = _normalized_resolver_evidence_payload(query_fragment)
    memory_payload = _normalized_resolver_evidence_payload(memory_fragment)
    if not query_payload or not memory_payload:
        return False
    if query_payload == memory_payload:
        return True
    negative_markers = (
        "not",
        "never",
        "no",
        "without",
        "禁止",
        "不得",
        "不允许",
        "不能",
        "不要",
    )
    if _contains_intent_marker(
        query_payload, negative_markers
    ) != _contains_intent_marker(memory_payload, negative_markers):
        return False
    if _explicit_binary_conflict(query_payload, memory_payload) is not None:
        return False
    query_actions = _resolver_action_families(query_payload)
    memory_actions = _resolver_action_families(memory_payload)
    if query_actions != memory_actions:
        return False
    query_anchors = _resolver_operation_anchors(query_payload)
    memory_anchors = _resolver_operation_anchors(memory_payload)
    shared = query_anchors & memory_anchors
    union = query_anchors | memory_anchors
    return bool(
        len(shared) >= 2
        and union
        and len(shared) * 4 >= len(union) * 3
    )


def _normalized_resolver_evidence_payload(value: object) -> str:
    """Normalize one typed proof segment without keeping its source label."""

    text = _normalized(value, MAX_CLASSIFIER_CONTENT_CHARS).casefold()
    label, separator, payload = text.partition(":")
    if separator and label.strip() in {
        "task",
        "content",
        "category",
        "tags",
        "domains",
        "metadata",
        "current_files",
        "active_domains",
    }:
        text = payload
    return text.strip(" \t\r\n.,;:!?，。；：！？`'\"")


def _resolver_cross_language_relation_is_bound(
    query_fragment: str,
    memory_fragment: str,
    entry: Any,
) -> bool:
    """Require an approved explicit bilingual alias, not a third model vote."""

    metadata = _safe_metadata(getattr(entry, "metadata", {}))
    if str(metadata.get("relation") or "").casefold() not in {
        "cross_language_equivalence",
        "translation_alias",
    }:
        return False
    alias_from = _normalized_resolver_evidence_payload(
        metadata.get("alias_from") or ""
    )
    alias_to = _normalized_resolver_evidence_payload(
        metadata.get("alias_to") or ""
    )
    if not alias_from or not alias_to:
        return False
    query_folded = _normalized_resolver_evidence_payload(query_fragment)
    memory_folded = _normalized_resolver_evidence_payload(memory_fragment)
    return bool(
        (alias_from == query_folded and alias_to == memory_folded)
        or (alias_to == query_folded and alias_from == memory_folded)
    )


def _resolver_support_is_authorized(
    resolution: dict[str, Any],
    *,
    decision: dict[str, Any],
    entry: Any,
    request: Any,
) -> bool:
    support = resolution["supportKind"]
    query_kind = resolution.get("querySegmentKind")
    memory_kind = resolution.get("memorySegmentKind")
    primary_reason = decision.get("reasonCode")
    query_task = query_kind == "query.task"
    query_object = query_task
    memory_rule = memory_kind == "memory.content"
    evidence_fragments = _resolver_evidence_fragments(
        resolution,
        entry=entry,
        request=request,
    )
    if support == "exact_file_context":
        if evidence_fragments is None:
            return False
        query_fragment, memory_fragment = evidence_fragments
        shared_paths = _query_paths(request) & _entry_paths(entry)
        query_anchors = _resolver_operation_anchors(query_fragment)
        memory_anchors = _resolver_operation_anchors(memory_fragment)
        query_actions = _resolver_action_families(query_fragment)
        memory_actions = _resolver_action_families(memory_fragment)
        full_query = _canonical_request_fields(request)["query"]
        full_memory = _normalized(
            getattr(entry, "content", ""),
            MAX_CLASSIFIER_CONTENT_CHARS,
        )
        full_query_actions = _resolver_action_families(full_query)
        full_memory_actions = _resolver_action_families(full_memory)
        shared_anchors = query_anchors & memory_anchors
        # ``exact_file_context`` is intentionally a narrow typed proof, not a
        # general same-file escape hatch.  The only currently registered
        # relation is retry-timing recovery: both complete evidence surfaces
        # must expose that family, and no second action family may hide an
        # unknown or opposed primary operation behind a shared minor verb.
        typed_relation_is_complete = bool(
            full_query_actions == {"retry_timing"}
            and "retry_timing" in full_memory_actions
            and full_memory_actions
            <= {"retry_timing", "execution_start"}
        )
        relation_is_bound = bool(
            query_actions
            and memory_actions
            and shared_anchors
            and query_actions.intersection(memory_actions)
            and typed_relation_is_complete
            and not _resolver_action_families_conflict(
                full_query_actions,
                full_memory_actions,
            )
        )
        cited_content_paths = _paths_in_text(memory_fragment)
        return bool(
            query_task
            and memory_kind == "memory.content"
            and primary_reason
            in {"semantic_equivalence", "correction", "multi_clause", "constraint", "configuration"}
            and shared_paths
            and bool(shared_paths & cited_content_paths)
            and relation_is_bound
        )
    if support == "cross_language_equivalence":
        return bool(
            query_task
            and memory_kind == "memory.content"
            and primary_reason in {"semantic_equivalence", "alias", "correction"}
            and evidence_fragments is not None
            and _resolver_cross_language_relation_is_bound(
                *evidence_fragments,
                entry=entry,
            )
        )
    if support == "semantic_equivalence":
        return bool(
            query_task
            and memory_kind == "memory.content"
            and primary_reason
            in {"semantic_equivalence", "alias", "correction", "multi_clause"}
            and evidence_fragments is not None
            and _resolver_semantic_relation_is_bound(*evidence_fragments)
        )
    if support == "verified_recovery":
        return bool(
            query_object
            and memory_rule
            and primary_reason == "recovery"
            and _entry_has_verified_recovery(entry)
            and evidence_fragments is not None
            and _resolver_recovery_relation_is_bound(*evidence_fragments)
        )
    if support == "explicit_constraint":
        return bool(
            query_object
            and memory_rule
            and primary_reason in {"constraint", "multi_clause"}
            and evidence_fragments is not None
            and _resolver_constraint_relation_is_bound(*evidence_fragments)
        )
    if support == "explicit_configuration":
        return bool(
            query_object
            and memory_rule
            and primary_reason in {"configuration", "constraint", "multi_clause"}
            and evidence_fragments is not None
            and _resolver_configuration_relation_is_bound(*evidence_fragments)
        )
    if support == "established_preference":
        return bool(
            query_task
            and memory_kind == "memory.content"
            and primary_reason in {"preference", "correction"}
            and _entry_has_established_preference(entry)
            and evidence_fragments is not None
            and _resolver_semantic_relation_is_bound(*evidence_fragments)
        )
    if support == "verified_rename":
        return bool(
            query_object
            and memory_rule
            and primary_reason in {"rename", "alias", "correction"}
            and _deterministic_applicability_acceptance(request, entry)
            == "proven_rename"
        )
    return False


def _resolution_is_approved(
    resolution: dict[str, Any] | None,
    *,
    decision: dict[str, Any],
    entry: Any,
    request: Any,
    minimum_confidence: float | None,
) -> bool:
    if resolution is None or minimum_confidence is None:
        return False
    if (
        resolution.get("contestedReason")
        in HYBRID_RESOLVER_NON_OVERRIDABLE_VETO_REASONS
    ):
        return False
    return bool(
        resolution["decision"] == "relevant"
        and resolution["queryGrounded"] is True
        and resolution["objectMatch"] is True
        and resolution["relationSupported"] is True
        and resolution["supportKind"] != "none"
        and resolution["disqualifier"] == "none"
        and float(resolution["confidence"]) >= minimum_confidence
        and _resolver_support_is_authorized(
            resolution,
            decision=decision,
            entry=entry,
            request=request,
        )
        and _semantic_decision_has_required_authority(decision, entry)
        and (
            decision["reasonCode"] == "recovery"
            or resolution["supportKind"] != "verified_recovery"
        )
    )


@dataclass(frozen=True)
class _DenseRecord:
    digest: str
    vector: tuple[float, ...]


class _HybridSharedState:
    """Immutable encoder identity plus bounded, synchronized dense/query state."""

    def __init__(self, encoder: EmbeddingEncoder) -> None:
        self.encoder = encoder
        self.records: dict[str, _DenseRecord] = {}
        self.lock = threading.RLock()
        # Providers created for concurrent turns keep their own model adapter
        # and call budget, while this lock joins identical query work and makes
        # the shared adjudication cache race-free.
        self.adjudication_lock = threading.RLock()
        self.cache: OrderedDict[str, HybridAdjudication] = OrderedDict()


class LocalE5Encoder:
    """Pinned ONNX encoder whose manifest and every model file are verified."""

    def __init__(self, root: Path, expected_identity: dict[str, Any]) -> None:
        unresolved_root = Path(root).expanduser()
        if unresolved_root.is_symlink():
            raise ValueError("hybrid model root must be a real local directory")
        self.root = unresolved_root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("hybrid model root must be a real local directory")
        manifest_path = self.root / "model_manifest.json"
        if (
            not manifest_path.is_file()
            or manifest_path.is_symlink()
            or manifest_path.stat().st_size > MAX_MANIFEST_BYTES
        ):
            raise ValueError("hybrid model manifest is missing or unsafe")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("hybrid model manifest is malformed")
        for field in ("model_id", "model_revision", "model_fingerprint"):
            if manifest.get(field) != expected_identity.get(field):
                raise ValueError("hybrid model identity mismatch")
        self._identity = {
            field: manifest[field]
            for field in ("model_id", "model_revision", "model_fingerprint")
        }
        fingerprint_body = {
            key: value for key, value in manifest.items() if key != "model_fingerprint"
        }
        if _sha256_bytes(
            _stable_json(fingerprint_body).encode("utf-8")
        ) != manifest.get("model_fingerprint"):
            raise ValueError("hybrid model manifest fingerprint mismatch")
        files = manifest.get("files")
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_MODEL_FILES:
            raise ValueError("hybrid model file manifest is malformed")
        required = {"config.json", "tokenizer.json", "onnx/model_quantized.onnx"}
        if not required <= set(files):
            raise ValueError("hybrid model manifest misses required files")
        for relative, expected_digest in files.items():
            relative_path = Path(str(relative))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError("hybrid model path traversal is forbidden")
            path = self.root / relative_path
            resolved = path.resolve(strict=True)
            parents = relative_path.parts[:-1]
            current = self.root
            has_symlink_parent = False
            for part in parents:
                current = current / part
                if current.is_symlink():
                    has_symlink_parent = True
                    break
            if (
                self.root not in resolved.parents
                or path.is_symlink()
                or has_symlink_parent
                or not path.is_file()
            ):
                raise ValueError("hybrid model file escapes the model root")
            if _sha256_file(path) != expected_digest:
                raise ValueError("hybrid model file hash mismatch")
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "hybrid retrieval requires numpy, onnxruntime, and tokenizers"
            ) from exc
        self._np = np
        self._tokenizer = Tokenizer.from_file(str(self.root / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=512)
        padding_id = self._tokenizer.token_to_id("<pad>")
        if isinstance(padding_id, bool) or not isinstance(padding_id, int):
            raise ValueError("hybrid tokenizer has no valid padding token")
        self._tokenizer.enable_padding(pad_id=padding_id, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(self.root / "onnx" / "model_quantized.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.dimension = int(config.get("hidden_size", 0))
        if self.dimension <= 0:
            raise ValueError("hybrid model dimension is invalid")

    @property
    def identity(self) -> dict[str, Any]:
        """Return the verified local model identity behind this encoder."""
        return dict(self._identity)

    def encode_queries(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._encode(texts, "query")

    def encode_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._encode(texts, "passage")

    def _encode(
        self, texts: Sequence[str], prefix: str
    ) -> tuple[tuple[float, ...], ...]:
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("hybrid embedding input must be non-empty text")
        output: list[tuple[float, ...]] = []
        for start in range(0, len(texts), DEFAULT_BATCH_SIZE):
            batch = [
                f"{prefix}: {text}"
                for text in texts[start : start + DEFAULT_BATCH_SIZE]
            ]
            encodings = self._tokenizer.encode_batch(batch)
            input_ids = self._np.asarray(
                [item.ids for item in encodings], dtype=self._np.int64
            )
            attention = self._np.asarray(
                [item.attention_mask for item in encodings], dtype=self._np.int64
            )
            feeds: dict[str, Any] = {}
            for item in self._session.get_inputs():
                if item.name == "input_ids":
                    feeds[item.name] = input_ids
                elif item.name == "attention_mask":
                    feeds[item.name] = attention
                elif item.name == "token_type_ids":
                    feeds[item.name] = self._np.zeros_like(input_ids)
                else:
                    raise ValueError("unsupported hybrid ONNX input")
            hidden = self._session.run(None, feeds)[0]
            if hidden.ndim != 3:
                raise ValueError("unexpected hybrid ONNX output")
            mask = attention[..., None].astype(hidden.dtype)
            pooled = (hidden * mask).sum(axis=1) / self._np.maximum(
                mask.sum(axis=1), 1.0
            )
            norms = self._np.linalg.norm(pooled, axis=1, keepdims=True)
            if self._np.any(norms <= 1e-12):
                raise ValueError("hybrid embedding has zero norm")
            pooled = pooled / norms
            output.extend(tuple(float(value) for value in row) for row in pooled)
        if any(
            len(vector) != self.dimension
            or not all(math.isfinite(value) for value in vector)
            for vector in output
        ):
            raise ValueError("hybrid embedding output is invalid")
        return tuple(output)


class HybridRuntimeProvider:
    def __init__(
        self,
        *,
        encoder: EmbeddingEncoder,
        model_adapter: Any,
        dense_top_k: int,
        max_candidates: int,
        minimum_confidence: float,
        challenger_minimum_confidence: float = 0.8,
        resolver_minimum_confidence: float | None = None,
        max_model_calls: int = DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        embedding_provider: str = "local-e5",
        shared_state: _HybridSharedState | None = None,
        provider_cache_reused: bool = False,
        hot_store: MemoryHotStore | None = None,
        hot_cache_identity: str | None = None,
    ) -> None:
        if (
            isinstance(dense_top_k, bool)
            or not isinstance(dense_top_k, int)
            or dense_top_k != DEFAULT_DENSE_TOP_K
        ):
            raise ValueError(
                f"dense_top_k must equal the promoted value {DEFAULT_DENSE_TOP_K}"
            )
        if (
            isinstance(max_candidates, bool)
            or not isinstance(max_candidates, int)
            or max_candidates != MAX_CANDIDATES
        ):
            raise ValueError(
                f"max_candidates must equal the promoted value {MAX_CANDIDATES}"
            )
        state = shared_state or _HybridSharedState(encoder)
        self._shared_state = state
        self._encoder = state.encoder
        self._model = model_adapter
        self._dense_top_k = dense_top_k
        self._max_candidates = max_candidates
        self._minimum_confidence = minimum_confidence
        self._challenger_minimum_confidence = challenger_minimum_confidence
        self._resolver_minimum_confidence = resolver_minimum_confidence
        self._max_model_calls = max_model_calls
        self._embedding_provider = str(embedding_provider).strip().lower()
        self._model_calls = 0
        self._model_call_budget_owner: Any = None
        self._records = state.records
        self._lock = state.lock
        self._adjudication_lock = state.adjudication_lock
        self._cache = state.cache
        self._provider_cache_reused = bool(provider_cache_reused)
        self._hot_store = hot_store
        self._hot_cache_identity = hot_cache_identity

    def _reserve_model_call(self) -> None:
        if self._model_calls >= self._max_model_calls:
            raise RuntimeError("hybrid verifier model-call budget exhausted")
        self._model_calls += 1

    def _refresh(
        self,
        entries: tuple[Any, ...],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        eligible = {
            entry.id: entry for entry in entries if _entry_is_dense_eligible(entry)
        }
        changed: list[tuple[Any, str, str]] = []
        with self._lock:
            for entry_id, entry in eligible.items():
                document = _entry_document(entry)
                digest = _sha256_bytes(document.encode("utf-8"))
                current = self._records.get(entry_id)
                if current is None or current.digest != digest:
                    changed.append((entry, document, digest))
        vectors = (
            self._encode_with_context(
                self._encoder.encode_documents,
                [item[1] for item in changed],
                call_context={
                    **(call_context or {}),
                    "purpose": "memory_hybrid_embedding_documents",
                },
            )
            if changed
            else ()
        )
        with self._lock:
            retained = {
                entry_id: record
                for entry_id, record in self._records.items()
                if entry_id in eligible
            }
            self._records.clear()
            self._records.update(retained)
            for (entry, _document, digest), vector in zip(
                changed, vectors, strict=True
            ):
                self._records[entry.id] = _DenseRecord(digest, vector)
        return eligible

    @staticmethod
    def _cache_key(
        request: Any,
        entries: tuple[Any, ...],
        lexical_accepted_ids: Sequence[str] | frozenset[str],
    ) -> str:
        authority: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in sorted(entries, key=lambda item: item.id):
            if entry.id in seen:
                raise ValueError("hybrid snapshot contains duplicate entry IDs")
            seen.add(entry.id)
            tier = getattr(entry, "tier", None)
            scope = getattr(entry, "scope", None)
            approval_policy = getattr(entry, "approval_policy", None)
            semantic_authority = {
                # These fields do not all affect dense similarity, but they can
                # change exact-gate or resolver authorization.  Bind them to the
                # adjudication cache without including mutable feedback counters.
                "scope": getattr(scope, "value", scope),
                "source": getattr(entry, "source", ""),
                "metadata": getattr(entry, "metadata", {}),
                "provenance": getattr(entry, "provenance", {}),
                "approval_content_hash": getattr(
                    entry, "approval_content_hash", ""
                ),
                "approval_reason": getattr(entry, "approval_reason", ""),
                "approval_actor": getattr(entry, "approval_actor", ""),
                "approval_decided_at": getattr(
                    entry, "approval_decided_at", 0.0
                ),
                "approval_policy": getattr(
                    approval_policy, "value", approval_policy
                ),
                "safety_reason": getattr(entry, "safety_reason", ""),
                "deprecated_at": getattr(entry, "deprecated_at", None),
                "tier_reason": getattr(entry, "tier_reason", ""),
                "related_to": getattr(entry, "related_to", ()),
            }
            authority.append(
                {
                    "id": entry.id,
                    "approval": getattr(entry, "approval_status", ""),
                    "lifecycle": getattr(entry, "lifecycle_status", ""),
                    "safety": getattr(entry, "safety_status", ""),
                    "locked": bool(getattr(entry, "curator_locked", False)),
                    "tier": getattr(tier, "value", tier),
                    "document_sha256": _sha256_bytes(
                        _stable_json(
                            _canonical_entry_fields(
                                entry,
                                content_limit=MAX_CONTENT_CHARS,
                            )
                        ).encode("utf-8")
                    ),
                    "semantic_authority_sha256": _sha256_bytes(
                        _stable_json(semantic_authority).encode("utf-8")
                    ),
                }
            )
        payload = {
            "query": _canonical_request_fields(request),
            "query_projection_complete": _bounded_projection_is_complete(
                getattr(request, "query", ""),
                MAX_QUERY_CHARS,
            ),
            "lexical_accepted_ids": list(
                _ordered_lexical_candidate_ids(lexical_accepted_ids)
            ),
            "authority": authority,
        }
        return _sha256_bytes(_stable_json(payload).encode("utf-8"))

    def _revision_cache_key(
        self,
        request: Any,
        lexical_accepted_ids: Sequence[str] | frozenset[str],
        authority_revision: str,
    ) -> str:
        # The revision is supplied with the immutable retrieval snapshot. The
        # bounded request includes paths/domains and lexical rescue order;
        # normalizing only the free-text query would alias different decisions.
        payload = {
            "schema": "memory_hot_adjudication_v1",
            "authority_revision": authority_revision,
            "query": _canonical_request_fields(request),
            "query_projection_complete": _bounded_projection_is_complete(
                getattr(request, "query", ""), MAX_QUERY_CHARS
            ),
            "lexical_accepted_ids": list(
                _ordered_lexical_candidate_ids(lexical_accepted_ids)
            ),
            "provider": self._hot_cache_identity,
            "protocol": HYBRID_PROTOCOL_VERSION,
            "query_gate": HYBRID_QUERY_GATE_VERSION,
            "prompts": [
                [HYBRID_PROMPT_VERSION, HYBRID_SYSTEM_PROMPT],
                [HYBRID_CHALLENGER_PROMPT_VERSION, HYBRID_CHALLENGER_SYSTEM_PROMPT],
                [HYBRID_RESOLVER_PROMPT_VERSION, HYBRID_RESOLVER_SYSTEM_PROMPT],
            ],
            "configuration": [
                self._dense_top_k,
                self._max_candidates,
                self._minimum_confidence,
                self._challenger_minimum_confidence,
                self._resolver_minimum_confidence,
                self._max_model_calls,
                self._embedding_provider,
            ],
        }
        return _sha256_bytes(_stable_json(payload).encode("utf-8"))

    @staticmethod
    def _serialize_adjudication(result: HybridAdjudication) -> dict[str, Any]:
        return {
            "schema": "memory_hot_adjudication_v1",
            "signals": [
                {
                    "entry_id": signal.entry_id,
                    "dense_score": signal.dense_score,
                    "relevance_score": signal.relevance_score,
                    "accepted": signal.accepted,
                    "reason_codes": list(signal.reason_codes),
                }
                for signal in result.signals
            ],
            "diagnostics": result.diagnostics,
            "candidate_ids": (
                None if result.candidate_ids is None else list(result.candidate_ids)
            ),
        }

    @staticmethod
    def _deserialize_adjudication(
        value: dict[str, Any] | None,
        entries: tuple[Any, ...],
    ) -> HybridAdjudication | None:
        # Redis is disposable external state. Reject malformed/coerced values
        # instead of letting truthy strings or unknown IDs grant admission.
        if not isinstance(value, dict) or set(value) != {
            "schema", "signals", "diagnostics", "candidate_ids"
        }:
            return None
        if value["schema"] != "memory_hot_adjudication_v1":
            return None
        signals = value["signals"]
        candidate_ids = value["candidate_ids"]
        diagnostics = value["diagnostics"]
        active_ids = {entry.id for entry in entries}
        if (
            not isinstance(signals, list)
            or len(signals) > len(active_ids)
            or not isinstance(diagnostics, dict)
            or not all(isinstance(key, str) for key in diagnostics)
            or not isinstance(candidate_ids, list)
            or not all(isinstance(item, str) and item for item in candidate_ids)
        ):
            return None
        parsed: list[HybridCandidateSignal] = []
        try:
            # Validate nested diagnostic numbers as well as signal scores.
            json.dumps(diagnostics, allow_nan=False)
            for signal in signals:
                if not isinstance(signal, dict) or set(signal) != {
                    "entry_id", "dense_score", "relevance_score", "accepted",
                    "reason_codes",
                }:
                    return None
                if (
                    not isinstance(signal["entry_id"], str)
                    or signal["entry_id"] not in active_ids
                    or type(signal["accepted"]) is not bool
                    or any(
                        type(signal[field]) not in (int, float)
                        for field in ("dense_score", "relevance_score")
                    )
                    or not isinstance(signal["reason_codes"], list)
                    or not all(
                        isinstance(reason, str) and 0 < len(reason) <= 64
                        for reason in signal["reason_codes"]
                    )
                ):
                    return None
                parsed.append(HybridCandidateSignal(**signal))
            return HybridAdjudication(tuple(parsed), diagnostics, tuple(candidate_ids))
        except (TypeError, ValueError, OverflowError):
            return None

    def _cached(self, key: str) -> HybridAdjudication | None:
        with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            self._cache.move_to_end(key)
        return HybridAdjudication(
            cached.signals,
            {
                **cached.diagnostics,
                "provider_cache_reused": self._provider_cache_reused,
                "cache_hit": True,
                "model_call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "verifier_model_call_count": 0,
                "verifier_input_tokens": 0,
                "verifier_output_tokens": 0,
                "challenger_model_call_count": 0,
                "challenger_input_tokens": 0,
                "challenger_output_tokens": 0,
                "resolver_model_call_count": 0,
                "resolver_input_tokens": 0,
                "resolver_output_tokens": 0,
            },
            cached.candidate_ids,
        )

    def _remember(self, key: str, result: HybridAdjudication) -> None:
        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)

    def adjudicate(
        self,
        *,
        request: Any,
        entries: tuple[Any, ...],
        lexical_accepted_ids: Sequence[str] | frozenset[str],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
        authority_revision: str | None = None,
    ) -> HybridAdjudication | None:
        if not getattr(request, "query", "").strip():
            return None
        hot_store = self._hot_store
        with self._adjudication_lock, (
            hot_store.budget() if hot_store is not None else nullcontext()
        ):
            try:
                # Without a turn-owned AgentTurnBudget this public call is the
                # only reliable task boundary.  Do not let one query consume a
                # later query's local verifier allowance.  Calls that share an
                # explicit budget continue to share one allowance as intended.
                if agent_budget is None:
                    self._model_calls = 0
                    self._model_call_budget_owner = None
                elif self._model_call_budget_owner is not agent_budget:
                    self._model_calls = 0
                    self._model_call_budget_owner = agent_budget
                use_revision = bool(
                    hot_store is not None
                    and self._hot_cache_identity
                    and isinstance(authority_revision, str)
                    and authority_revision
                )
                cache_key = (
                    self._revision_cache_key(
                        request, lexical_accepted_ids, authority_revision
                    )
                    if use_revision
                    else self._cache_key(request, entries, lexical_accepted_ids)
                )
                shared = None
                if use_revision and hot_store.available:
                    shared = self._deserialize_adjudication(
                        hot_store.get_adjudication(cache_key), entries
                    )
                    if shared is not None:
                        self._remember(cache_key, shared)
                cached = self._cached(cache_key)
                if cached is not None:
                    if use_revision and hot_store.available and shared is None:
                        hot_store.put_adjudication(
                            cache_key, self._serialize_adjudication(cached)
                        )
                    return cached
                result = self._adjudicate_uncached(
                    request=request,
                    entries=entries,
                    lexical_accepted_ids=lexical_accepted_ids,
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                self._remember(cache_key, result)
                if use_revision and hot_store.available:
                    hot_store.put_adjudication(
                        cache_key, self._serialize_adjudication(result)
                    )
                return result
            except (TurnCancellationRequested, ModelCallDeadlineExceeded):
                raise
            except Exception:
                return None

    def _adjudicate_uncached(
        self,
        *,
        request: Any,
        entries: tuple[Any, ...],
        lexical_accepted_ids: Sequence[str] | frozenset[str],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> HybridAdjudication:
        entry_ids = {entry.id for entry in entries}
        lexical_order = _ordered_lexical_candidate_ids(lexical_accepted_ids)
        lexical_id_set = set(lexical_order)
        if not lexical_id_set <= entry_ids:
            raise ValueError("hybrid lexical candidates are outside the snapshot")
        query_projection_complete = _bounded_projection_is_complete(
            getattr(request, "query", ""),
            MAX_QUERY_CHARS,
        )
        if not query_projection_complete or not _query_has_concrete_object(request):
            candidate_ids = lexical_order[: self._max_candidates]
            candidate_id_set = set(candidate_ids)
            budget_rejected_ids = [
                entry_id
                for entry_id in lexical_order
                if entry_id not in candidate_id_set
            ]
            query_gate = (
                "input_budget"
                if not query_projection_complete
                else "underspecified"
            )
            rejection_reason = (
                "hybrid_query_input_budget"
                if not query_projection_complete
                else "hybrid_query_underspecified"
            )
            return HybridAdjudication(
                tuple(
                    HybridCandidateSignal(
                        entry_id=entry_id,
                        dense_score=0.0,
                        relevance_score=0.0,
                        accepted=False,
                        reason_codes=(rejection_reason,),
                    )
                    for entry_id in candidate_ids
                ),
                {
                    "provider": f"{self._embedding_provider}_strict_llm_v3",
                    "provider_cache_reused": self._provider_cache_reused,
                    "cache_hit": False,
                    "query_gate": query_gate,
                    "dense_candidate_count": 0,
                    "dense_lane_count": 0,
                    "lexical_rescue_count": len(candidate_ids),
                    "candidate_count": len(candidate_ids),
                    "adjudicated_count": 0,
                    "deterministic_accepted_count": 0,
                    "deterministic_acceptance_reasons": {},
                    "deterministic_rejected_count": 0,
                    "deterministic_rejection_reasons": {},
                    "query_gate_rejected_count": len(candidate_ids),
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
                    "memory_input_budget_rejected_count": 0,
                    "candidate_budget_rejected_count": len(budget_rejected_ids),
                    "model_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "verifier_model_call_count": 0,
                    "verifier_input_tokens": 0,
                    "verifier_output_tokens": 0,
                    "challenger_model_call_count": 0,
                    "challenger_input_tokens": 0,
                    "challenger_output_tokens": 0,
                    "resolver_model_call_count": 0,
                    "resolver_input_tokens": 0,
                    "resolver_output_tokens": 0,
                },
                candidate_ids,
            )
        call_context = {
            "agent_budget": agent_budget,
            "event_sink": event_sink,
            "cancellation_token": cancellation_token,
            "deadline_monotonic": deadline_monotonic,
        }
        eligible = self._refresh(entries, call_context=call_context)
        query_vector = self._encode_with_context(
            self._encoder.encode_queries,
            [_query_document(request)],
            call_context={
                **call_context,
                "purpose": "memory_hybrid_embedding_query",
            },
        )[0]
        with self._lock:
            scores = [
                (
                    entry_id,
                    sum(
                        left * right
                        for left, right in zip(query_vector, record.vector, strict=True)
                    ),
                )
                for entry_id, record in self._records.items()
            ]
        scores.sort(key=lambda item: (-item[1], item[0]))
        score_map = dict(scores)
        dense_order = [entry_id for entry_id, _score in scores]
        safe_lexical_ids = [
            entry_id for entry_id in lexical_order if entry_id in eligible
        ]
        # Dense retrieval owns a protected recall lane.  Lexical evidence is a
        # bounded rescue lane, never a prefix that can crowd semantic matches
        # out of the adjudication budget. Inputs that cannot be represented
        # completely to the classifier are rejected while filling each lane,
        # so they cannot occupy a slot and hide the next valid candidate.
        dense_lane, dense_input_rejected_ids = _take_complete_candidate_lane(
            dense_order,
            eligible=eligible,
            limit=min(self._dense_top_k, self._max_candidates),
        )
        dense_seen = set(dense_lane) | set(dense_input_rejected_ids)
        lexical_rescue, lexical_input_rejected_ids = (
            _take_complete_candidate_lane(
                safe_lexical_ids,
                eligible=eligible,
                limit=max(0, self._max_candidates - len(dense_lane)),
                excluded_ids=dense_seen,
            )
        )
        memory_input_budget_rejected_ids = list(
            dict.fromkeys(
                (*dense_input_rejected_ids, *lexical_input_rejected_ids)
            )
        )
        memory_input_budget_rejected_id_set = set(
            memory_input_budget_rejected_ids
        )
        candidate_ids = [*dense_lane, *lexical_rescue]
        included = set(candidate_ids)
        exact_gate_rejected_ids = [
            entry_id for entry_id in lexical_order if entry_id not in eligible
        ]
        budget_rejected_ids = [
            entry_id
            for entry_id in safe_lexical_ids
            if entry_id not in included
            and entry_id not in memory_input_budget_rejected_id_set
        ]
        signals = [
            HybridCandidateSignal(
                entry_id=entry_id,
                dense_score=0.0,
                relevance_score=0.0,
                accepted=False,
                reason_codes=("hybrid_ineligible_exact_gate",),
            )
            for entry_id in exact_gate_rejected_ids
        ]
        signals.extend(
            HybridCandidateSignal(
                entry_id=entry_id,
                dense_score=max(
                    0.0, min(1.0, (score_map.get(entry_id, -1.0) + 1.0) / 2.0)
                ),
                relevance_score=0.0,
                accepted=False,
                reason_codes=("hybrid_candidate_budget",),
            )
            for entry_id in budget_rejected_ids
        )
        signals.extend(
            HybridCandidateSignal(
                entry_id=entry_id,
                dense_score=max(
                    0.0,
                    min(1.0, (score_map.get(entry_id, -1.0) + 1.0) / 2.0),
                ),
                relevance_score=0.0,
                accepted=False,
                reason_codes=("hybrid_memory_input_budget",),
            )
            for entry_id in memory_input_budget_rejected_ids
        )
        # A migration proves object/path continuity only; it can never waive a
        # contradictory operation, polarity, version, or safety instruction.
        # Compute vetoes first and permit deterministic acceptance only for
        # candidates with no applicable rejection.
        deterministic_rejections = {
            entry_id: reason
            for entry_id in candidate_ids
            if entry_id not in memory_input_budget_rejected_id_set
            if (
                reason := _deterministic_applicability_rejection(
                    request, eligible[entry_id]
                )
            )
        }
        deterministic_acceptances = {
            entry_id: reason
            for entry_id in candidate_ids
            if entry_id not in memory_input_budget_rejected_id_set
            if entry_id not in deterministic_rejections
            if (
                reason := _deterministic_applicability_acceptance(
                    request, eligible[entry_id]
                )
            )
        }
        adjudication_ids = [
            entry_id
            for entry_id in candidate_ids
            if entry_id not in memory_input_budget_rejected_id_set
            and entry_id not in deterministic_rejections
            and entry_id not in deterministic_acceptances
        ]
        signals.extend(
            HybridCandidateSignal(
                entry_id=entry_id,
                dense_score=max(
                    0.0,
                    min(1.0, (score_map.get(entry_id, -1.0) + 1.0) / 2.0),
                ),
                relevance_score=1.0,
                accepted=True,
                reason_codes=(f"deterministic_{reason}",),
            )
            for entry_id, reason in deterministic_acceptances.items()
        )
        signals.extend(
            HybridCandidateSignal(
                entry_id=entry_id,
                dense_score=max(
                    0.0,
                    min(1.0, (score_map.get(entry_id, -1.0) + 1.0) / 2.0),
                ),
                relevance_score=0.0,
                accepted=False,
                reason_codes=(f"deterministic_{reason}",),
            )
            for entry_id, reason in deterministic_rejections.items()
        )
        verifier_usage = {
            "model_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        challenger_usage = dict(verifier_usage)
        resolver_usage = dict(verifier_usage)
        usage = dict(verifier_usage)
        preliminary_accepted_ids: list[str] = []
        audits: dict[str, dict[str, Any]] = {}
        resolutions: dict[str, dict[str, Any]] = {}
        resolver_attempted_ids: list[str] = []
        resolver_input_rejected_count = 0
        resolver_approved_ids: set[str] = set()
        challenger_approved_ids: set[str] = set()
        semantic_accepted_ids: set[str] = set()
        if adjudication_ids:
            decisions, verifier_usage = self._verify(
                request=request,
                entries=[eligible[entry_id] for entry_id in adjudication_ids],
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            usage = dict(verifier_usage)
            preliminary_accepted_ids = [
                entry_id
                for entry_id in adjudication_ids
                if (
                    decisions[entry_id]["decision"] == "relevant"
                    and decisions[entry_id]["objectMatch"] is True
                    and decisions[entry_id]["relationSupported"] is True
                    and float(decisions[entry_id]["confidence"])
                    >= self._minimum_confidence
                    and _semantic_decision_has_required_authority(
                        decisions[entry_id], eligible[entry_id]
                    )
                )
            ]
            if preliminary_accepted_ids:
                audits, challenger_usage = self._challenge(
                    request=request,
                    entries=[
                        eligible[entry_id] for entry_id in preliminary_accepted_ids
                    ],
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                for key in usage:
                    usage[key] += challenger_usage[key]
                challenger_approved_ids = {
                    entry_id
                    for entry_id, audit in audits.items()
                    if audit["admit"] is True
                    and audit["reasonCode"] == "no_disqualifier"
                    and float(audit["confidence"])
                    >= self._challenger_minimum_confidence
                }
                disagreement_ids = [
                    entry_id
                    for entry_id in preliminary_accepted_ids
                    if (
                        (audit := audits.get(entry_id)) is not None
                        and audit["admit"] is False
                        and float(audit["confidence"])
                        >= self._challenger_minimum_confidence
                        and audit["reasonCode"]
                        not in HYBRID_RESOLVER_NON_OVERRIDABLE_VETO_REASONS
                        and self._resolver_minimum_confidence is not None
                    )
                ]
                if disagreement_ids:
                    resolver_attempted_ids = list(disagreement_ids)
                    resolutions, resolver_usage = self._resolve_disagreements(
                        request=request,
                        entries=[eligible[entry_id] for entry_id in disagreement_ids],
                        decisions=decisions,
                        audits=audits,
                        agent_budget=agent_budget,
                        event_sink=event_sink,
                        cancellation_token=cancellation_token,
                        deadline_monotonic=deadline_monotonic,
                    )
                    resolver_input_rejected_count = resolver_usage.get(
                        "input_rejected_count", 0
                    )
                    for key in ("model_call_count", "input_tokens", "output_tokens"):
                        usage[key] += resolver_usage[key]
                    resolver_approved_ids = {
                        entry_id
                        for entry_id, resolution in resolutions.items()
                        if _resolution_is_approved(
                            resolution,
                            decision=decisions[entry_id],
                            entry=eligible[entry_id],
                            request=request,
                            minimum_confidence=self._resolver_minimum_confidence,
                        )
                    }
            semantic_accepted_ids = set(preliminary_accepted_ids) & (
                challenger_approved_ids | resolver_approved_ids
            )
            for entry_id in adjudication_ids:
                decision = decisions[entry_id]
                confidence = float(decision["confidence"])
                audit = audits.get(entry_id)
                challenge_confidence = (
                    float(audit["confidence"]) if audit is not None else 0.0
                )
                challenger_approved = entry_id in challenger_approved_ids
                resolution = resolutions.get(entry_id)
                resolver_approved = entry_id in resolver_approved_ids
                accepted = entry_id in preliminary_accepted_ids and (
                    challenger_approved or resolver_approved
                )
                reasons = [f"semantic_{decision['reasonCode']}"]
                if not _semantic_decision_has_required_authority(
                    decision, eligible[entry_id]
                ):
                    reasons.append(
                        "semantic_unverified_cause"
                        if decision.get("reasonCode") == "cause"
                        else "semantic_unverified_recovery"
                    )
                if audit is not None:
                    reasons.append(f"challenge_{audit['reasonCode']}")
                    if challenge_confidence < self._challenger_minimum_confidence:
                        reasons.append("challenge_below_confidence")
                elif entry_id in preliminary_accepted_ids:
                    reasons.append("challenge_missing_consent")
                if resolution is not None:
                    if resolution.get("runtimeReason") == (
                        "resolver_input_budget_rejected"
                    ):
                        reasons.append("resolver_input_budget_rejected")
                    elif resolver_approved:
                        reasons.append("resolver_proven_relevant")
                    elif (
                        resolution["decision"] == "relevant"
                        and float(resolution["confidence"])
                        < self._resolver_minimum_confidence
                    ):
                        reasons.append("resolver_below_confidence")
                    elif resolution["decision"] == "relevant":
                        reasons.append("resolver_support_not_authorized")
                    else:
                        reasons.append(f"resolver_{resolution['disqualifier']}")
                    if (
                        resolution["decision"] == "relevant"
                        and decision["reasonCode"] in {"cause", "recovery"}
                        and not _semantic_decision_has_required_authority(
                            decision,
                            eligible[entry_id],
                        )
                    ):
                        reasons.append(
                            "resolver_unverified_cause"
                            if decision["reasonCode"] == "cause"
                            else "resolver_unverified_recovery"
                        )
                signals.append(
                    HybridCandidateSignal(
                        entry_id=entry_id,
                        dense_score=max(
                            0.0,
                            min(1.0, (score_map.get(entry_id, -1.0) + 1.0) / 2.0),
                        ),
                        relevance_score=confidence,
                        accepted=accepted,
                        reason_codes=tuple(reasons),
                    )
                )
        return HybridAdjudication(
            tuple(signals),
            {
                "provider": f"{self._embedding_provider}_strict_llm_v3",
                "provider_cache_reused": self._provider_cache_reused,
                "cache_hit": False,
                "dense_candidate_count": len(dense_lane),
                "dense_lane_count": len(dense_lane),
                "lexical_rescue_count": len(lexical_rescue),
                "candidate_count": len(candidate_ids),
                "adjudicated_count": len(adjudication_ids),
                "deterministic_accepted_count": len(deterministic_acceptances),
                "deterministic_acceptance_reasons": dict(
                    sorted(deterministic_acceptances.items())
                ),
                "deterministic_rejected_count": len(deterministic_rejections),
                "deterministic_rejection_reasons": dict(
                    sorted(deterministic_rejections.items())
                ),
                "query_gate_rejected_count": 0,
                "preliminary_accepted_count": len(preliminary_accepted_ids),
                "challenged_count": len(audits),
                "challenger_accepted_count": len(challenger_approved_ids),
                "resolver_attempted_count": len(resolver_attempted_ids),
                "resolver_input_rejected_count": resolver_input_rejected_count,
                "resolver_disagreement_count": len(resolutions),
                "resolver_accepted_count": len(resolver_approved_ids),
                "semantic_accepted_count": len(semantic_accepted_ids),
                "accepted_count": len(deterministic_acceptances)
                + len(semantic_accepted_ids),
                "exact_gate_rejected_count": len(exact_gate_rejected_ids),
                "memory_input_budget_rejected_count": len(
                    memory_input_budget_rejected_ids
                ),
                "candidate_budget_rejected_count": len(budget_rejected_ids),
                "verifier_model_call_count": verifier_usage["model_call_count"],
                "verifier_input_tokens": verifier_usage["input_tokens"],
                "verifier_output_tokens": verifier_usage["output_tokens"],
                "challenger_model_call_count": challenger_usage[
                    "model_call_count"
                ],
                "challenger_input_tokens": challenger_usage["input_tokens"],
                "challenger_output_tokens": challenger_usage["output_tokens"],
                "resolver_model_call_count": resolver_usage["model_call_count"],
                "resolver_input_tokens": resolver_usage["input_tokens"],
                "resolver_output_tokens": resolver_usage["output_tokens"],
                **usage,
            },
            tuple(candidate_ids),
        )

    def _verify(
        self,
        *,
        request: Any,
        entries: list[Any],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        decisions: list[dict[str, Any]] = []
        usage = {"model_call_count": 0, "input_tokens": 0, "output_tokens": 0}
        request_fields = _canonical_request_fields(request)
        for start in range(0, len(entries), DEFAULT_BATCH_SIZE):
            batch = entries[start : start + DEFAULT_BATCH_SIZE]
            pairs = [
                {
                    "id": entry.id,
                    "query": request_fields["query"],
                    "currentFiles": request_fields["current_files"],
                    "activeDomains": request_fields["active_domains"],
                    "memory": {
                        **_canonical_entry_fields(entry),
                        "verifiedRecoveryAuthority": (
                            _entry_has_verified_recovery(entry)
                        ),
                        "verifiedCauseAuthority": _entry_has_verified_cause(entry),
                    },
                }
                for entry in batch
            ]
            saved_thinking = getattr(self._model, "_thinking_blocks", None)
            if saved_thinking is not None:
                self._model._thinking_blocks = []
            try:
                step, parsed_decisions = self._invoke_model(
                    [
                        {"role": "system", "content": HYBRID_SYSTEM_PROMPT},
                        {"role": "user", "content": _stable_json({"pairs": pairs})},
                    ],
                    purpose="memory_hybrid_verifier",
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    response_validator=lambda content, expected_ids=[
                        entry.id for entry in batch
                    ]: _parse_decisions(content, expected_ids),
                )
            finally:
                if saved_thinking is not None:
                    self._model._thinking_blocks = saved_thinking
            decisions.extend(parsed_decisions)
            usage["model_call_count"] += 1
            step_usage = getattr(step, "usage", None)
            if step_usage is not None:
                usage["input_tokens"] += getattr(step_usage, "input_tokens", 0) or 0
                usage["output_tokens"] += getattr(step_usage, "output_tokens", 0) or 0
        by_id = {row["id"]: row for row in decisions}
        if len(by_id) != len(decisions) or len(by_id) != len(entries):
            raise ValueError("hybrid verifier emitted duplicate decisions")
        return by_id, usage

    def _challenge(
        self,
        *,
        request: Any,
        entries: list[Any],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        audits: list[dict[str, Any]] = []
        usage = {"model_call_count": 0, "input_tokens": 0, "output_tokens": 0}
        request_fields = _canonical_request_fields(request)
        for start in range(0, len(entries), DEFAULT_BATCH_SIZE):
            batch = entries[start : start + DEFAULT_BATCH_SIZE]
            pairs = [
                {
                    "id": entry.id,
                    "query": request_fields["query"],
                    "currentFiles": request_fields["current_files"],
                    "activeDomains": request_fields["active_domains"],
                    "memory": {
                        **_canonical_entry_fields(entry),
                        "verifiedRecoveryAuthority": (
                            _entry_has_verified_recovery(entry)
                        ),
                        "verifiedCauseAuthority": _entry_has_verified_cause(entry),
                    },
                }
                for entry in batch
            ]
            saved_thinking = getattr(self._model, "_thinking_blocks", None)
            if saved_thinking is not None:
                self._model._thinking_blocks = []
            try:
                step, parsed_audits = self._invoke_model(
                    [
                        {
                            "role": "system",
                            "content": HYBRID_CHALLENGER_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": _stable_json({"pairs": pairs})},
                    ],
                    purpose="memory_hybrid_challenger",
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    response_validator=lambda content, expected_ids=[
                        entry.id for entry in batch
                    ]: _parse_audits(content, expected_ids),
                )
            finally:
                if saved_thinking is not None:
                    self._model._thinking_blocks = saved_thinking
            audits.extend(parsed_audits)
            usage["model_call_count"] += 1
            step_usage = getattr(step, "usage", None)
            if step_usage is not None:
                usage["input_tokens"] += getattr(step_usage, "input_tokens", 0) or 0
                usage["output_tokens"] += getattr(step_usage, "output_tokens", 0) or 0
        by_id = {row["id"]: row for row in audits}
        if len(by_id) != len(audits) or len(by_id) != len(entries):
            raise ValueError("hybrid challenger emitted duplicate audits")
        return by_id, usage

    def _resolve_disagreements(
        self,
        *,
        request: Any,
        entries: list[Any],
        decisions: dict[str, dict[str, Any]],
        audits: dict[str, dict[str, Any]],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
        """Resolve one valid verifier/challenger disagreement exactly once."""

        resolutions: list[dict[str, Any]] = []
        usage = {
            "model_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "input_rejected_count": 0,
        }

        # Validate each pair independently before batching.  A single oversized
        # or unprojectable memory is denied locally instead of poisoning every
        # otherwise valid disagreement that happened to share its batch.
        prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for entry in entries:
            try:
                pairs, expected = _build_resolver_batch(
                    [(request, entry)],
                    decisions={entry.id: decisions[entry.id]},
                    audits={entry.id: audits[entry.id]},
                )
            except ValueError as error:
                message = str(error)
                if not any(
                    marker in message
                    for marker in (
                        "proof segment",
                        "proof input budget",
                        "has no proof segments",
                    )
                ):
                    raise
                resolutions.append(
                    _resolver_input_rejection(
                        str(entry.id),
                        contested_reason=str(audits[entry.id]["reasonCode"]),
                    )
                )
                usage["input_rejected_count"] += 1
                continue
            prepared.append((pairs[0], expected[entry.id]))

        batches: list[tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]] = []
        current_pairs: list[dict[str, Any]] = []
        current_expected: dict[str, dict[str, Any]] = {}
        for pair, expected_row in prepared:
            proposed = [*current_pairs, pair]
            if current_pairs and (
                len(proposed) > DEFAULT_BATCH_SIZE
                or len(_stable_json({"pairs": proposed}))
                > MAX_RESOLVER_CHARS_PER_BATCH
            ):
                batches.append((current_pairs, current_expected))
                current_pairs = []
                current_expected = {}
                proposed = [pair]
            if len(_stable_json({"pairs": proposed})) > MAX_RESOLVER_CHARS_PER_BATCH:
                # Defensive fail-closed guard if pair and batch limits drift.
                resolutions.append(
                    _resolver_input_rejection(
                        str(pair["id"]),
                        contested_reason=str(expected_row["contested_reason"]),
                    )
                )
                usage["input_rejected_count"] += 1
                continue
            current_pairs.append(pair)
            current_expected[str(pair["id"])] = expected_row
        if current_pairs:
            batches.append((current_pairs, current_expected))

        for pairs, expected in batches:
            saved_thinking = getattr(self._model, "_thinking_blocks", None)
            if saved_thinking is not None:
                self._model._thinking_blocks = []
            try:
                step, parsed_resolutions = self._invoke_model(
                    [
                        {
                            "role": "system",
                            "content": HYBRID_RESOLVER_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": _stable_json({"pairs": pairs})},
                    ],
                    purpose="memory_hybrid_resolver",
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                    response_validator=lambda content, expected=expected: (
                        _parse_resolutions(content, expected=expected)
                    ),
                )
            finally:
                if saved_thinking is not None:
                    self._model._thinking_blocks = saved_thinking
            resolutions.extend(parsed_resolutions)
            usage["model_call_count"] += 1
            step_usage = getattr(step, "usage", None)
            if step_usage is not None:
                usage["input_tokens"] += getattr(step_usage, "input_tokens", 0) or 0
                usage["output_tokens"] += getattr(step_usage, "output_tokens", 0) or 0
        by_id = {row["id"]: row for row in resolutions}
        if len(by_id) != len(resolutions) or len(by_id) != len(entries):
            raise ValueError("hybrid resolver emitted duplicate resolutions")
        return by_id, usage

    def _invoke_model(
        self,
        messages: list[dict[str, Any]],
        *,
        purpose: str,
        agent_budget: Any,
        event_sink: Any,
        cancellation_token: Any,
        deadline_monotonic: float | None,
        response_validator: Callable[[str], Any] | None = None,
    ) -> Any:
        reservation = None
        request_started = False
        operation_id = new_model_operation_id()
        started_at = time.monotonic()
        emit_event_safely(
            event_sink,
            "model.started",
            payload={"operationId": operation_id, "purpose": purpose},
        )
        try:
            if agent_budget is not None:
                from minicode.context_manager import estimate_messages_tokens

                reservation = agent_budget.reserve_model_call(
                    estimate_messages_tokens(messages),
                    preserve_model_calls=1,
                )
            self._reserve_model_call()
            checkpoint_model_call(
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            request_started = True
            step = call_model_next(
                self._model,
                messages,
                optional_kwargs={
                    "disable_tools": True,
                    "isolated_reasoning_state": True,
                },
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            self._validate_completed_classifier_step(step)
            validated_response = None
            if response_validator is not None:
                try:
                    validated_response = response_validator(step.content)
                except HybridResponseProtocolError:
                    raise
                except (AssertionError, KeyError, TypeError, ValueError) as error:
                    raise HybridResponseProtocolError(
                        "hybrid classifier response schema is invalid"
                    ) from error
        except Exception as error:
            settle = getattr(agent_budget, "fail_model_call", None)
            if callable(settle):
                settle(reservation, charge_estimate=request_started)
            payload: dict[str, object] = {
                "operationId": operation_id,
                "purpose": purpose,
                "failureKind": (
                    "budget_exhausted"
                    if isinstance(error, AgentBudgetExceeded)
                    else "timeout"
                    if isinstance(error, ModelCallDeadlineExceeded)
                    else "interrupted"
                    if isinstance(error, TurnCancellationRequested)
                    else "response_protocol_error"
                    if isinstance(error, HybridResponseProtocolError)
                    else "provider_error"
                ),
            }
            duration = self._duration_ms(started_at)
            if duration is not None:
                payload["durationMs"] = duration
            emit_event_safely(event_sink, "model.failed", payload=payload)
            raise

        usage = project_model_usage(getattr(step, "usage", None))
        try:
            cost_payload = project_model_cost_event(
                model=self._model,
                usage=usage,
                operation_id=operation_id,
            )
        except BaseException:  # noqa: BLE001 - observation stays optional
            cost_payload = pricing_failure_event_payload(operation_id)
        if agent_budget is not None:
            record_budgeted_model_call(
                agent_budget,
                model=self._model,
                usage=usage,
                reservation=reservation,
                cost_payload=cost_payload,
            )
        completed: dict[str, object] = {
            "operationId": operation_id,
            "purpose": purpose,
            "resultType": "assistant",
            "contentPresent": bool(getattr(step, "content", "")),
            "toolCallCount": 0,
            "usage": usage,
        }
        duration = self._duration_ms(started_at)
        if duration is not None:
            completed["durationMs"] = duration
        emit_event_safely(event_sink, "model.completed", payload=completed)
        cost_payload["purpose"] = purpose
        emit_event_safely(event_sink, "model.costed", payload=cost_payload)
        if response_validator is None:
            return step
        return step, validated_response

    def _validate_completed_classifier_step(self, step: Any) -> None:
        """Accept only a complete, terminal, text-only classifier response.

        A syntactically complete JSON prefix is not trustworthy when a provider
        stopped because of truncation, filtering, or a tool request.  The Qwen
        promotion route is OpenAI-compatible and reports a successful terminal
        text response as ``type=assistant`` with ``stopReason=stop``.
        """

        if getattr(step, "type", None) != "assistant":
            raise HybridResponseProtocolError(
                "hybrid classifier did not return an assistant step"
            )
        calls = getattr(step, "calls", None)
        if not isinstance(calls, list) or calls:
            raise HybridResponseProtocolError(
                "hybrid classifier returned tool calls"
            )
        diagnostics = getattr(step, "diagnostics", None)
        complete_reasons = (
            {"stop"} if self._embedding_provider == "qwen" else {"stop", "end_turn"}
        )
        if (
            diagnostics is None
            or getattr(diagnostics, "stopReason", None) not in complete_reasons
        ):
            raise HybridResponseProtocolError(
                "hybrid classifier response was not terminal"
            )
        if {"tool_calls", "tool_use"}.intersection(
            tuple(getattr(diagnostics, "blockTypes", ()) or ())
        ):
            raise HybridResponseProtocolError(
                "hybrid classifier response contained tool calls"
            )

    @staticmethod
    def _encode_with_context(
        encoder_method: Any,
        texts: Sequence[str],
        *,
        call_context: dict[str, Any],
    ) -> tuple[tuple[float, ...], ...]:
        kwargs: dict[str, Any] = {}
        try:
            signature = inspect.signature(encoder_method)
            if "call_context" in signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            ):
                kwargs["call_context"] = call_context
        except (TypeError, ValueError):
            pass
        return encoder_method(texts, **kwargs)

    @staticmethod
    def _duration_ms(started_at: float) -> int | None:
        try:
            return project_model_duration_ms(started_at, time.monotonic())
        except BaseException:  # noqa: BLE001 - observation stays optional
            return None


def _strict_model_json(value: str) -> Any:
    def build_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("hybrid model JSON contains a duplicate key")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=build_object)


_DECISION_KEYS = frozenset(
    {
        "id",
        "decision",
        "confidence",
        "objectMatch",
        "relationSupported",
        "reasonCode",
    }
)
_AUDIT_KEYS = frozenset({"id", "admit", "confidence", "reasonCode"})


def _parse_decisions(text: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    import re

    match = re.search(r"\{.*\}", str(text).strip(), re.DOTALL)
    if match is None:
        raise ValueError("hybrid verifier returned no JSON")
    payload = _strict_model_json(match.group(0))
    rows = payload.get("decisions") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"decisions"}
        or not isinstance(rows, list)
        or len(rows) != len(expected_ids)
    ):
        raise ValueError("hybrid verifier decision count mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _DECISION_KEYS:
            raise ValueError("hybrid verifier decision is malformed")
        confidence = row.get("confidence")
        if (
            not isinstance(row.get("id"), str)
            or row.get("decision") not in HYBRID_ALLOWED_DECISIONS
            or row.get("reasonCode") not in HYBRID_ALLOWED_REASONS
            or not isinstance(row.get("objectMatch"), bool)
            or not isinstance(row.get("relationSupported"), bool)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
            or (
                row.get("decision") == "relevant"
                and (
                    row.get("reasonCode") not in HYBRID_RELEVANT_REASONS
                    or row.get("objectMatch") is not True
                    or row.get("relationSupported") is not True
                )
            )
            or (
                row.get("decision") == "irrelevant"
                and (
                    row.get("reasonCode") not in HYBRID_IRRELEVANT_REASONS
                    or (
                        row.get("objectMatch") is True
                        and row.get("relationSupported") is True
                    )
                )
            )
        ):
            raise ValueError("hybrid verifier decision is malformed")
        result.append(dict(row))
    if sorted(row["id"] for row in result) != sorted(expected_ids):
        raise ValueError("hybrid verifier IDs mismatch")
    return result


def _parse_audits(text: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    match = re.search(r"\{.*\}", str(text).strip(), re.DOTALL)
    if match is None:
        raise ValueError("hybrid challenger returned no JSON")
    payload = _strict_model_json(match.group(0))
    rows = payload.get("audits") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"audits"}
        or not isinstance(rows, list)
        or len(rows) != len(expected_ids)
    ):
        raise ValueError("hybrid challenger audit count mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _AUDIT_KEYS:
            raise ValueError("hybrid challenger audit is malformed")
        confidence = row.get("confidence")
        if (
            not isinstance(row.get("id"), str)
            or not isinstance(row.get("admit"), bool)
            or row.get("reasonCode") not in HYBRID_CHALLENGER_ALLOWED_REASONS
            or (row.get("admit") is True and row.get("reasonCode") != "no_disqualifier")
            or (
                row.get("admit") is False
                and row.get("reasonCode") not in HYBRID_CHALLENGER_VETO_REASONS
            )
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ValueError("hybrid challenger audit is malformed")
        result.append(dict(row))
    if sorted(row["id"] for row in result) != sorted(expected_ids):
        raise ValueError("hybrid challenger IDs mismatch")
    return result


_RESOLUTION_KEYS = frozenset(
    {
        "id",
        "decision",
        "confidence",
        "queryGrounded",
        "objectMatch",
        "relationSupported",
        "supportKind",
        "disqualifier",
        "queryEvidenceId",
        "memoryEvidenceId",
    }
)


def _proof_segment_source(segments: Any, evidence_id: Any) -> dict[str, Any]:
    if (
        not isinstance(segments, dict)
        or not isinstance(evidence_id, str)
        or evidence_id not in segments
        or not isinstance(segments[evidence_id], dict)
        or set(segments[evidence_id]) != {"start", "end", "kind"}
    ):
        raise ValueError("hybrid resolver proof segment ID is invalid")
    span = segments[evidence_id]
    if (
        isinstance(span["start"], bool)
        or not isinstance(span["start"], int)
        or isinstance(span["end"], bool)
        or not isinstance(span["end"], int)
        or span["start"] < 0
        or span["end"] <= span["start"]
        or not isinstance(span["kind"], str)
        or span["kind"]
        not in (
            set(_QUERY_SEGMENT_KINDS.values()) | set(_MEMORY_SEGMENT_KINDS.values())
        )
    ):
        raise ValueError("hybrid resolver proof segment span is invalid")
    return dict(span)


def _parse_resolutions(
    text: str,
    *,
    expected: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    match = re.search(r"\{.*\}", str(text).strip(), re.DOTALL)
    if match is None:
        raise ValueError("hybrid resolver returned no JSON")
    payload = _strict_model_json(match.group(0))
    rows = payload.get("resolutions") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"resolutions"}
        or not isinstance(rows, list)
        or len(rows) != len(expected)
    ):
        raise ValueError("hybrid resolver resolution count mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _RESOLUTION_KEYS:
            raise ValueError("hybrid resolver resolution is malformed")
        entry_id = row.get("id")
        confidence = row.get("confidence")
        decision = row.get("decision")
        if not isinstance(entry_id, str) or entry_id not in expected:
            raise ValueError("hybrid resolver resolution ID is invalid")
        if decision not in HYBRID_ALLOWED_DECISIONS:
            raise ValueError("hybrid resolver decision is invalid")
        contested_reason = expected[entry_id]["contested_reason"]
        if (
            contested_reason not in HYBRID_CHALLENGER_VETO_REASONS
            or contested_reason in HYBRID_RESOLVER_NON_OVERRIDABLE_VETO_REASONS
        ):
            raise ValueError("hybrid resolver contested reason is invalid")
        if not isinstance(row.get("queryGrounded"), bool):
            raise ValueError("hybrid resolver query grounding is invalid")
        if not isinstance(row.get("objectMatch"), bool):
            raise ValueError("hybrid resolver object match is invalid")
        if not isinstance(row.get("relationSupported"), bool):
            raise ValueError("hybrid resolver relation support is invalid")
        if row.get("supportKind") not in HYBRID_RESOLVER_SUPPORT_KINDS:
            raise ValueError("hybrid resolver support kind is invalid")
        if row.get("disqualifier") not in (
            {"none"} | set(HYBRID_CHALLENGER_VETO_REASONS)
        ):
            raise ValueError("hybrid resolver disqualifier is invalid")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ValueError("hybrid resolver confidence is invalid")
        if decision == "relevant" and (
            row["queryGrounded"] is not True
            or row["objectMatch"] is not True
            or row["relationSupported"] is not True
            or row["supportKind"] == "none"
            or row["disqualifier"] != "none"
        ):
            raise ValueError("hybrid resolver relevant proof is incomplete")
        if decision == "irrelevant" and row["disqualifier"] == "none":
            raise ValueError("hybrid resolver rejection lacks a disqualifier")
        query_source = _proof_segment_source(
            expected[entry_id]["query_segments"], row["queryEvidenceId"]
        )
        memory_source = _proof_segment_source(
            expected[entry_id]["memory_segments"], row["memoryEvidenceId"]
        )
        if not query_source["kind"].startswith("query.") or not memory_source[
            "kind"
        ].startswith("memory."):
            raise ValueError("hybrid resolver proof segment side is invalid")
        projected = {
            key: value
            for key, value in row.items()
            if key not in {"queryEvidenceId", "memoryEvidenceId"}
        }
        # The contested reason is immutable, pair-bound input. Re-attach it
        # after validating the model's narrower output so downstream approval
        # remains explicit without relying on a redundant model echo.
        projected["contestedReason"] = contested_reason
        projected["querySpan"] = {
            "start": query_source["start"],
            "end": query_source["end"],
        }
        projected["memorySpan"] = {
            "start": memory_source["start"],
            "end": memory_source["end"],
        }
        projected["querySegmentKind"] = query_source["kind"]
        projected["memorySegmentKind"] = memory_source["kind"]
        result.append(projected)
    if sorted(row["id"] for row in result) != sorted(expected):
        raise ValueError("hybrid resolver IDs mismatch")
    return result


def _model_id(model_adapter: Any) -> str:
    direct = getattr(model_adapter, "model_id", "")
    if direct:
        return str(direct)
    runtime = getattr(model_adapter, "runtime", {})
    return str(runtime.get("model", "")) if isinstance(runtime, dict) else ""


def _model_adapter_inference_profile(
    model_adapter: Any,
) -> dict[str, int | float | bool] | None:
    """Project the exact inference controls used by the verifier adapter.

    The promotion receipt names evidence-style snake-case fields, while the
    OpenAI-compatible adapter consumes canonical runtime camel-case fields.
    Missing fields and look-alike aliases must not inherit adapter/provider
    defaults because those defaults were not evaluated by the promotion run.
    """

    runtime = getattr(model_adapter, "runtime", None)
    if not isinstance(runtime, dict):
        return None
    max_output_tokens = runtime.get("maxOutputTokens")
    temperature = runtime.get("temperature")
    max_retries = runtime.get("modelMaxRetries")
    timeout_seconds = runtime.get("modelTimeoutSeconds")
    enable_thinking = runtime.get("enableThinking")
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or max_output_tokens <= 0
        or isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(float(temperature))
        or isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or max_retries < 0
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
        or not isinstance(enable_thinking, bool)
    ):
        return None
    return {
        "max_output_tokens": max_output_tokens,
        "temperature": float(temperature),
        "max_retries": max_retries,
        "timeout_seconds": float(timeout_seconds),
        "enable_thinking": enable_thinking,
    }


def _salted_digest(value: object) -> str:
    serialized = _stable_json(value).encode("utf-8")
    return _sha256_bytes(_PROVIDER_CACHE_SALT + serialized)


def _stable_digest(value: object) -> str:
    # Shared Redis identities must survive process restarts. Only the final
    # digest is sent to Redis; credentials and route details stay local.
    return _sha256_bytes(_stable_json(value).encode("utf-8"))


def _explicit_cache_identity(owner: Any) -> object | None:
    identity = getattr(owner, "hybrid_runtime_cache_identity", None)
    if callable(identity):
        try:
            identity = identity()
        except Exception:
            return None
    if isinstance(identity, (str, int, float, bool, list, tuple, dict)):
        return identity
    return None


def _model_adapter_cache_identity(
    model_adapter: Any, *, persistent: bool = False
) -> dict[str, Any] | None:
    digest = _stable_digest if persistent else _salted_digest
    adapter_type = (
        f"{type(model_adapter).__module__}.{type(model_adapter).__qualname__}"
    )
    explicit = _explicit_cache_identity(model_adapter)
    runtime = getattr(model_adapter, "runtime", None)
    if not isinstance(runtime, dict):
        if explicit is None:
            return None
        return {
            "model_id": _model_id(model_adapter),
            "explicit_digest": digest(
                {"adapter_type": adapter_type, "identity": explicit}
            ),
        }
    credential_fields = (
        "apiKey",
        "authToken",
        "openaiApiKey",
        "openrouterApiKey",
        "customApiKey",
    )
    credentials = [
        str(runtime.get(name) or "")
        for name in credential_fields
        if str(runtime.get(name) or "")
    ]
    if not credentials and explicit is None:
        # Without a bound credential (or an explicit identity supplied by a
        # local adapter) rotation cannot be detected, so cross-turn reuse is
        # deliberately disabled.
        return None
    identity_fields = (
        "model",
        "provider",
        "baseUrl",
        "openaiBaseUrl",
        "openrouterBaseUrl",
        "customBaseUrl",
        "temperature",
        "maxOutputTokens",
        "modelTimeoutSeconds",
        "modelMaxRetries",
        "disableThinking",
        "enableThinking",
        "_custom_headers",
        "_openrouter_headers",
        "_openrouter_params",
    )
    identity_payload = {
        name: runtime.get(name) for name in identity_fields if name in runtime
    }
    identity_payload["adapter_type"] = adapter_type
    if explicit is not None:
        identity_payload["explicit_identity"] = explicit
    identity_payload["credentials"] = {
        name: runtime.get(name)
        for name in credential_fields
        if str(runtime.get(name) or "")
    }
    try:
        runtime_digest = digest(identity_payload)
    except (TypeError, ValueError):
        # Unstable or unserializable transport configuration disables sharing
        # instead of falling back to an incomplete cache identity.
        return None
    return {
        "model_id": _model_id(model_adapter),
        "runtime_digest": runtime_digest,
    }


def _embedding_client_cache_identity(
    client: Any, *, persistent: bool = False
) -> dict[str, Any] | None:
    digest = _stable_digest if persistent else _salted_digest
    explicit = _explicit_cache_identity(client)
    model = str(getattr(client, "model", "") or getattr(client, "_model", ""))
    endpoint = (
        str(getattr(client, "endpoint", "") or getattr(client, "_endpoint", ""))
        .strip()
        .rstrip("/")
    )
    if explicit is not None:
        return {
            "model": model,
            "endpoint": endpoint,
            "explicit_digest": digest(explicit),
        }
    credential = getattr(client, "_api_key", None)
    if not model or not endpoint or not isinstance(credential, str) or not credential:
        return None
    return {
        "model": model,
        "endpoint": endpoint,
        "credential_digest": digest(credential),
    }


def _provider_cache_key(
    *,
    activation: HybridActivation,
    model_adapter: Any,
    workspace_path: str | Path | None,
    embedding_client: Any | None,
    persistent: bool = False,
) -> str | None:
    adapter_identity = _model_adapter_cache_identity(
        model_adapter, persistent=persistent
    )
    if adapter_identity is None or activation.evidence is None:
        return None
    embedding_identity = (
        _embedding_client_cache_identity(embedding_client, persistent=persistent)
        if embedding_client is not None
        else {
            "provider": activation.embedding_provider,
            "model_path": str(activation.model_path or ""),
        }
    )
    if embedding_identity is None:
        return None
    workspace = (
        str(Path(workspace_path).expanduser().resolve(strict=False))
        if workspace_path is not None
        else ""
    )
    payload = {
        "workspace": workspace,
        "evidence": activation.evidence,
        "evidence_path": str(activation.evidence_path or ""),
        "embedding_provider": activation.embedding_provider,
        "embedding": embedding_identity,
        "verifier": adapter_identity,
    }
    return (_stable_digest if persistent else _salted_digest)(payload)


def _cached_provider_state(key: str | None) -> _HybridSharedState | None:
    if key is None:
        return None
    with _PROVIDER_CACHE_LOCK:
        state = _PROVIDER_CACHE.get(key)
        if state is not None:
            _PROVIDER_CACHE.move_to_end(key)
        return state


def _remember_provider_state(key: str | None, state: _HybridSharedState) -> None:
    if key is None:
        return
    with _PROVIDER_CACHE_LOCK:
        _PROVIDER_CACHE[key] = state
        _PROVIDER_CACHE.move_to_end(key)
        while len(_PROVIDER_CACHE) > MAX_PROVIDER_CACHE_ENTRIES:
            _PROVIDER_CACHE.popitem(last=False)


def clear_hybrid_provider_cache() -> None:
    """Clear process-local Hybrid state (primarily for isolated tests)."""
    with _PROVIDER_CACHE_LOCK:
        _PROVIDER_CACHE.clear()


def _provider_initialization_failure(
    reason: str,
    *,
    strict: bool,
) -> None:
    if strict:
        raise HybridProviderInitializationError(reason)
    return None


def create_hybrid_candidate_provider(
    *,
    activation: HybridActivation,
    model_adapter: Any,
    workspace_path: str | Path | None = None,
    embedding_client_factory: Any | None = None,
    strict: bool = False,
) -> HybridRuntimeProvider | None:
    if not activation.active or activation.evidence is None:
        return _provider_initialization_failure("activation_unavailable", strict=strict)
    evidence = activation.evidence
    if activation.embedding_provider == "qwen":
        route = evidence.get("verifier_route")
        adapter_runtime = getattr(model_adapter, "runtime", None)
        if (
            route != qwen_verifier_route_identity()
            or not isinstance(adapter_runtime, dict)
            or adapter_runtime.get("provider") != "custom"
            or str(adapter_runtime.get("customBaseUrl") or "").strip().rstrip("/")
            != QWEN_VERIFIER_BASE_URL
        ):
            return _provider_initialization_failure(
                "verifier_transport_identity_mismatch", strict=strict
            )
    if (
        evidence.get("inference_profile")
        != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
        or _model_adapter_inference_profile(model_adapter)
        != dict(HYBRID_PROMOTION_INFERENCE_PROFILE)
    ):
        return _provider_initialization_failure(
            "verifier_inference_profile_mismatch",
            strict=strict,
        )
    verifier = evidence.get("verifier")
    challenger = evidence.get("challenger")
    resolver = evidence.get("resolver")
    if (
        not isinstance(verifier, dict)
        or not isinstance(challenger, dict)
        or not isinstance(resolver, dict)
    ):
        return _provider_initialization_failure(
            "verifier_evidence_invalid", strict=strict
        )
    veto_reason_codes = challenger.get("veto_reason_codes")
    if not isinstance(veto_reason_codes, (list, tuple)):
        return _provider_initialization_failure(
            "challenger_evidence_invalid", strict=strict
        )
    prompt_sha = _sha256_bytes(HYBRID_SYSTEM_PROMPT.encode("utf-8"))
    challenger_sha = _sha256_bytes(HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8"))
    resolver_sha = _sha256_bytes(HYBRID_RESOLVER_SYSTEM_PROMPT.encode("utf-8"))
    if (
        verifier.get("prompt_version") != HYBRID_PROMPT_VERSION
        or verifier.get("prompt_sha256") != prompt_sha
        or verifier.get("model_id") != _model_id(model_adapter)
        or challenger.get("prompt_version") != HYBRID_CHALLENGER_PROMPT_VERSION
        or challenger.get("prompt_sha256") != challenger_sha
        or challenger.get("model_id") != _model_id(model_adapter)
        or challenger.get("mode") != HYBRID_CHALLENGER_MODE
        or set(veto_reason_codes) != HYBRID_CHALLENGER_VETO_REASONS
        or resolver.get("prompt_version") != HYBRID_RESOLVER_PROMPT_VERSION
        or resolver.get("prompt_sha256") != resolver_sha
        or resolver.get("model_id") != _model_id(model_adapter)
        or resolver.get("mode") != HYBRID_RESOLVER_MODE
        or resolver.get("segment_version") != HYBRID_RESOLVER_SEGMENT_VERSION
        or resolver.get("span_coordinate_system")
        != HYBRID_RESOLVER_SPAN_COORDINATE_SYSTEM
        or resolver.get("max_attempts_per_candidate") != 1
        or resolver.get("required_affirmatives") != 2
        or resolver.get("deterministic_veto_precedence") is not True
        or resolver.get("non_overridable_veto_reasons")
        != sorted(HYBRID_RESOLVER_NON_OVERRIDABLE_VETO_REASONS)
    ):
        reason = (
            "verifier_model_mismatch"
            if verifier.get("model_id") != _model_id(model_adapter)
            or challenger.get("model_id") != _model_id(model_adapter)
            or resolver.get("model_id") != _model_id(model_adapter)
            else "verifier_contract_mismatch"
        )
        return _provider_initialization_failure(reason, strict=strict)
    confidence = float(verifier.get("minimum_confidence", -1.0))
    challenger_confidence = float(challenger.get("minimum_confidence", -1.0))
    resolver_confidence = float(resolver.get("minimum_confidence", -1.0))
    dense_top_k = int(evidence.get("dense_top_k", DEFAULT_DENSE_TOP_K))
    max_candidates = int(evidence.get("max_union_candidates", MAX_CANDIDATES))
    max_model_calls = int(
        evidence.get("max_model_calls_per_task", DEFAULT_MAX_MODEL_CALLS_PER_TASK)
    )
    if (
        not 0.0 <= confidence <= 1.0
        or not 0.0 <= challenger_confidence <= 1.0
        or not 0.0 <= resolver_confidence <= 1.0
        or dense_top_k != DEFAULT_DENSE_TOP_K
        or max_candidates != MAX_CANDIDATES
        or not 1 <= max_model_calls <= 16
    ):
        return _provider_initialization_failure(
            "candidate_contract_invalid", strict=strict
        )
    hot_store = (
        get_memory_hot_store(workspace_path)
        if workspace_path is not None
        and os.environ.get("MINI_CODE_MEMORY_REDIS_URL", "").strip()
        else None
    )
    embedding_client = None
    if activation.embedding_provider == "qwen":
        factory = (
            embedding_client_factory
            if embedding_client_factory is not None
            else create_openai_compatible_embedding_client
        )
        embedding_client = factory(workspace_path)
        if embedding_client is None:
            return _provider_initialization_failure(
                "embedding_client_unavailable", strict=strict
            )
        evidence_model = evidence.get("model")
        configured_model = str(
            getattr(embedding_client, "model", "")
            or getattr(embedding_client, "_model", "")
        )
        configured_endpoint = str(
            getattr(embedding_client, "endpoint", "")
            or getattr(embedding_client, "_endpoint", "")
        ).rstrip("/")
        if (
            not isinstance(evidence_model, dict)
            or configured_model != str(evidence_model.get("model_id") or "")
            or configured_endpoint
            != str(evidence_model.get("endpoint") or "").rstrip("/")
        ):
            return _provider_initialization_failure(
                "embedding_transport_identity_mismatch", strict=strict
            )
        cache_key = _provider_cache_key(
            activation=activation,
            model_adapter=model_adapter,
            workspace_path=workspace_path,
            embedding_client=embedding_client,
        )
        hot_cache_identity = (
            _provider_cache_key(
                activation=activation,
                model_adapter=model_adapter,
                workspace_path=workspace_path,
                embedding_client=embedding_client,
                persistent=True,
            )
            if hot_store is not None
            else None
        )
        cached_state = _cached_provider_state(cache_key)
        if cached_state is not None:
            return HybridRuntimeProvider(
                encoder=cached_state.encoder,
                model_adapter=model_adapter,
                dense_top_k=dense_top_k,
                max_candidates=max_candidates,
                minimum_confidence=confidence,
                challenger_minimum_confidence=challenger_confidence,
                resolver_minimum_confidence=resolver_confidence,
                max_model_calls=max_model_calls,
                embedding_provider=activation.embedding_provider,
                shared_state=cached_state,
                provider_cache_reused=True,
                hot_store=hot_store,
                hot_cache_identity=hot_cache_identity,
            )
        encoder: EmbeddingEncoder = OpenAICompatibleEmbeddingEncoder(
            embedding_client,
            provider="qwen",
        )
        if encoder.identity != evidence.get("model"):
            return _provider_initialization_failure(
                "embedding_model_identity_mismatch", strict=strict
            )
    else:
        if activation.model_path is None:
            return _provider_initialization_failure(
                "local_model_unavailable", strict=strict
            )
        cache_key = _provider_cache_key(
            activation=activation,
            model_adapter=model_adapter,
            workspace_path=workspace_path,
            embedding_client=None,
        )
        hot_cache_identity = (
            _provider_cache_key(
                activation=activation,
                model_adapter=model_adapter,
                workspace_path=workspace_path,
                embedding_client=None,
                persistent=True,
            )
            if hot_store is not None
            else None
        )
        cached_state = _cached_provider_state(cache_key)
        if cached_state is not None:
            return HybridRuntimeProvider(
                encoder=cached_state.encoder,
                model_adapter=model_adapter,
                dense_top_k=dense_top_k,
                max_candidates=max_candidates,
                minimum_confidence=confidence,
                challenger_minimum_confidence=challenger_confidence,
                resolver_minimum_confidence=resolver_confidence,
                max_model_calls=max_model_calls,
                embedding_provider=activation.embedding_provider,
                shared_state=cached_state,
                provider_cache_reused=True,
                hot_store=hot_store,
                hot_cache_identity=hot_cache_identity,
            )
        encoder = LocalE5Encoder(activation.model_path, evidence["model"])
    shared_state = _HybridSharedState(encoder)
    provider = HybridRuntimeProvider(
        encoder=encoder,
        model_adapter=model_adapter,
        dense_top_k=dense_top_k,
        max_candidates=max_candidates,
        minimum_confidence=confidence,
        challenger_minimum_confidence=challenger_confidence,
        resolver_minimum_confidence=resolver_confidence,
        max_model_calls=max_model_calls,
        embedding_provider=activation.embedding_provider,
        shared_state=shared_state,
        hot_store=hot_store,
        hot_cache_identity=hot_cache_identity,
    )
    _remember_provider_state(cache_key, shared_state)
    return provider
