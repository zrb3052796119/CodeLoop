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
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from minicode.embeddings import (
    EmbeddingEncoder,
    OpenAICompatibleEmbeddingEncoder,
    create_openai_compatible_embedding_client,
)
from minicode.memory_hybrid import (
    HYBRID_CHALLENGER_ALLOWED_REASONS,
    HYBRID_CHALLENGER_MODE,
    HYBRID_CHALLENGER_PROMPT_VERSION,
    HYBRID_CHALLENGER_SYSTEM_PROMPT,
    HYBRID_CHALLENGER_VETO_REASONS,
    HYBRID_ALLOWED_DECISIONS,
    HYBRID_ALLOWED_REASONS,
    HYBRID_PROMPT_VERSION,
    HYBRID_SYSTEM_PROMPT,
    HybridActivation,
    HybridAdjudication,
    HybridCandidateSignal,
)
from minicode.agent_budget import AgentBudgetExceeded, record_budgeted_model_call
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
MAX_MODEL_FILES = 16
MAX_MANIFEST_BYTES = 128_000
MAX_CANDIDATES = 32
DEFAULT_DENSE_TOP_K = 20
DEFAULT_BATCH_SIZE = 20
MAX_CACHE_ENTRIES = 64
DEFAULT_MAX_MODEL_CALLS_PER_TASK = 8

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
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())[:limit]


def _bounded_strings(values: Any, *, limit: int = 16) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result = [_normalized(item, 256) for item in values[:limit]]
    return tuple(dict.fromkeys(item for item in result if item))


def _safe_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "path",
        "paths",
        "files",
        "file_paths",
        "config_key",
        "value",
        "renamed_from",
        "renamed_to",
        "migration_id",
        "correction_id",
        "supersedes",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, int, float, bool)):
            result[key] = _normalized(value, 256)
        elif isinstance(value, list):
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
    parts = [
        f"content: {_normalized(getattr(entry, 'content', ''), MAX_CONTENT_CHARS)}",
        f"category: {_normalized(getattr(entry, 'category', ''), 128)}",
    ]
    tags = _bounded_strings(getattr(entry, "tags", ()))
    domains = _bounded_strings(getattr(entry, "domains", ()))
    metadata = _safe_metadata(getattr(entry, "metadata", {}))
    if tags:
        parts.append(f"tags: {' | '.join(tags)}")
    if domains:
        parts.append(f"domains: {' | '.join(domains)}")
    if metadata:
        parts.append(f"metadata: {_stable_json(metadata)}")
    return "\n".join(parts)


def _query_document(request: Any) -> str:
    parts = [f"task: {_normalized(getattr(request, 'query', ''), 2_000)}"]
    files = _bounded_strings(getattr(request, "current_files", ()))
    domains = _bounded_strings(getattr(request, "active_domains", ()))
    if files:
        parts.append(f"current_files: {' | '.join(files)}")
    if domains:
        parts.append(f"active_domains: {' | '.join(domains)}")
    return "\n".join(parts)


def _query_has_concrete_object(request: Any) -> bool:
    if _bounded_strings(getattr(request, "current_files", ())):
        return True
    text = _normalized(getattr(request, "query", ""), 2_000).lower()
    if not text:
        return False
    if re.search(r"(?:[/\\]|\b[a-z0-9_-]+\.[a-z0-9]{1,12}\b)", text):
        return True
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9_-]*", text)
    if any(term not in _GENERIC_QUERY_TERMS for term in ascii_terms):
        return True
    non_ascii = "".join(character for character in text if ord(character) > 127)
    for generic in _GENERIC_CJK_QUERY_PARTS:
        non_ascii = non_ascii.replace(generic, "")
    return sum(character.isalpha() or character.isdigit() for character in non_ascii) >= 2


@dataclass(frozen=True)
class _DenseRecord:
    digest: str
    vector: tuple[float, ...]


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
        if _sha256_bytes(_stable_json(fingerprint_body).encode("utf-8")) != manifest.get(
            "model_fingerprint"
        ):
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
            batch = [f"{prefix}: {text}" for text in texts[start : start + DEFAULT_BATCH_SIZE]]
            encodings = self._tokenizer.encode_batch(batch)
            input_ids = self._np.asarray([item.ids for item in encodings], dtype=self._np.int64)
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
            pooled = (hidden * mask).sum(axis=1) / self._np.maximum(mask.sum(axis=1), 1.0)
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
        max_model_calls: int = DEFAULT_MAX_MODEL_CALLS_PER_TASK,
        embedding_provider: str = "local-e5",
    ) -> None:
        self._encoder = encoder
        self._model = model_adapter
        self._dense_top_k = dense_top_k
        self._max_candidates = max_candidates
        self._minimum_confidence = minimum_confidence
        self._challenger_minimum_confidence = challenger_minimum_confidence
        self._max_model_calls = max_model_calls
        self._embedding_provider = str(embedding_provider).strip().lower()
        self._model_calls = 0
        self._model_call_budget_owner: Any = None
        self._records: dict[str, _DenseRecord] = {}
        self._lock = threading.RLock()
        self._adjudication_lock = threading.RLock()
        self._cache: OrderedDict[str, HybridAdjudication] = OrderedDict()

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
        eligible = {entry.id: entry for entry in entries if _entry_is_dense_eligible(entry)}
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
            self._records = {
                entry_id: record
                for entry_id, record in self._records.items()
                if entry_id in eligible
            }
            for (entry, _document, digest), vector in zip(changed, vectors, strict=True):
                self._records[entry.id] = _DenseRecord(digest, vector)
        return eligible

    @staticmethod
    def _cache_key(
        request: Any,
        entries: tuple[Any, ...],
        lexical_accepted_ids: frozenset[str],
    ) -> str:
        authority: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in sorted(entries, key=lambda item: item.id):
            if entry.id in seen:
                raise ValueError("hybrid snapshot contains duplicate entry IDs")
            seen.add(entry.id)
            tier = getattr(entry, "tier", None)
            authority.append(
                {
                    "id": entry.id,
                    "approval": getattr(entry, "approval_status", ""),
                    "lifecycle": getattr(entry, "lifecycle_status", ""),
                    "safety": getattr(entry, "safety_status", ""),
                    "locked": bool(getattr(entry, "curator_locked", False)),
                    "tier": getattr(tier, "value", tier),
                    "document_sha256": _sha256_bytes(
                        _entry_document(entry).encode("utf-8")
                    ),
                }
            )
        payload = {
            "query": _query_document(request),
            "lexical_accepted_ids": sorted(lexical_accepted_ids),
            "authority": authority,
        }
        return _sha256_bytes(_stable_json(payload).encode("utf-8"))

    def _cached(self, key: str) -> HybridAdjudication | None:
        cached = self._cache.get(key)
        if cached is None:
            return None
        self._cache.move_to_end(key)
        return HybridAdjudication(
            cached.signals,
            {
                **cached.diagnostics,
                "cache_hit": True,
                "model_call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
        )

    def _remember(self, key: str, result: HybridAdjudication) -> None:
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def adjudicate(
        self,
        *,
        request: Any,
        entries: tuple[Any, ...],
        lexical_accepted_ids: frozenset[str],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> HybridAdjudication | None:
        if not getattr(request, "query", "").strip():
            return None
        with self._adjudication_lock:
            try:
                if (
                    agent_budget is not None
                    and self._model_call_budget_owner is not agent_budget
                ):
                    self._model_calls = 0
                    self._model_call_budget_owner = agent_budget
                cache_key = self._cache_key(request, entries, lexical_accepted_ids)
                cached = self._cached(cache_key)
                if cached is not None:
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
        lexical_accepted_ids: frozenset[str],
        agent_budget: Any = None,
        event_sink: Any = None,
        cancellation_token: Any = None,
        deadline_monotonic: float | None = None,
    ) -> HybridAdjudication:
        entry_ids = {entry.id for entry in entries}
        if not lexical_accepted_ids <= entry_ids:
            raise ValueError("hybrid lexical candidates are outside the snapshot")
        if not _query_has_concrete_object(request):
            return HybridAdjudication(
                tuple(
                    HybridCandidateSignal(
                        entry_id=entry_id,
                        dense_score=0.0,
                        relevance_score=0.0,
                        accepted=False,
                        reason_codes=("hybrid_query_underspecified",),
                    )
                    for entry_id in sorted(lexical_accepted_ids)
                ),
                {
                    "provider": f"{self._embedding_provider}_strict_llm_v2",
                    "cache_hit": False,
                    "query_gate": "underspecified",
                    "dense_candidate_count": 0,
                    "adjudicated_count": 0,
                    "model_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
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
        dense_ids = [entry_id for entry_id, _score in scores[: self._dense_top_k]]
        safe_lexical_ids = sorted(
            (entry_id for entry_id in lexical_accepted_ids if entry_id in eligible),
            key=lambda item: (-score_map.get(item, -1.0), item),
        )
        candidate_ids = list(
            dict.fromkeys((*safe_lexical_ids, *dense_ids))
        )[: self._max_candidates]
        included = set(candidate_ids)
        signals = [
            HybridCandidateSignal(
                entry_id=entry_id,
                dense_score=0.0,
                relevance_score=0.0,
                accepted=False,
                reason_codes=("hybrid_ineligible_exact_gate",),
            )
            for entry_id in sorted(lexical_accepted_ids - set(eligible))
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
            for entry_id in safe_lexical_ids
            if entry_id not in included
        )
        usage = {"model_call_count": 0, "input_tokens": 0, "output_tokens": 0}
        preliminary_accepted_ids: list[str] = []
        audits: dict[str, dict[str, Any]] = {}
        if candidate_ids:
            decisions, usage = self._verify(
                request=request,
                entries=[eligible[entry_id] for entry_id in candidate_ids],
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            preliminary_accepted_ids = [
                entry_id
                for entry_id in candidate_ids
                if (
                    decisions[entry_id]["decision"] == "relevant"
                    and decisions[entry_id]["objectMatch"] is True
                    and decisions[entry_id]["relationSupported"] is True
                    and float(decisions[entry_id]["confidence"])
                    >= self._minimum_confidence
                )
            ]
            if preliminary_accepted_ids:
                audits, challenge_usage = self._challenge(
                    request=request,
                    entries=[eligible[entry_id] for entry_id in preliminary_accepted_ids],
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
                for key in usage:
                    usage[key] += challenge_usage[key]
            for entry_id in candidate_ids:
                decision = decisions[entry_id]
                confidence = float(decision["confidence"])
                audit = audits.get(entry_id)
                challenge_confidence = (
                    float(audit["confidence"]) if audit is not None else 0.0
                )
                vetoed = bool(
                    audit is not None
                    and audit["admit"] is False
                    and audit["reasonCode"] in HYBRID_CHALLENGER_VETO_REASONS
                    and challenge_confidence >= self._challenger_minimum_confidence
                )
                accepted = entry_id in preliminary_accepted_ids and not vetoed
                reasons = [f"semantic_{decision['reasonCode']}"]
                if audit is not None:
                    reasons.append(f"challenge_{audit['reasonCode']}")
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
                "provider": f"{self._embedding_provider}_strict_llm_v2",
                "cache_hit": False,
                "dense_candidate_count": len(dense_ids),
                "adjudicated_count": len(candidate_ids),
                "preliminary_accepted_count": len(preliminary_accepted_ids),
                "challenged_count": len(audits),
                "exact_gate_rejected_count": len(signals) - len(candidate_ids),
                **usage,
            },
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
        for start in range(0, len(entries), DEFAULT_BATCH_SIZE):
            batch = entries[start : start + DEFAULT_BATCH_SIZE]
            pairs = [
                {
                    "id": entry.id,
                    "query": request.query,
                    "currentFiles": list(request.current_files),
                    "activeDomains": list(request.active_domains),
                    "memory": {
                        "content": _normalized(entry.content, 2_000),
                        "scope": entry.scope.value,
                        "category": _normalized(entry.category, 128),
                        "tags": list(_bounded_strings(entry.tags)),
                        "domains": list(_bounded_strings(entry.domains)),
                        "metadata": _safe_metadata(entry.metadata),
                    },
                }
                for entry in batch
            ]
            saved_thinking = getattr(self._model, "_thinking_blocks", None)
            if saved_thinking is not None:
                self._model._thinking_blocks = []
            try:
                step = self._invoke_model(
                    [
                        {"role": "system", "content": HYBRID_SYSTEM_PROMPT},
                        {"role": "user", "content": _stable_json({"pairs": pairs})},
                    ],
                    purpose="memory_hybrid_verifier",
                    agent_budget=agent_budget,
                    event_sink=event_sink,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                )
            finally:
                if saved_thinking is not None:
                    self._model._thinking_blocks = saved_thinking
            decisions.extend(_parse_decisions(step.content, [entry.id for entry in batch]))
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
        for start in range(0, len(entries), DEFAULT_BATCH_SIZE):
            batch = entries[start : start + DEFAULT_BATCH_SIZE]
            pairs = [
                {
                    "id": entry.id,
                    "query": request.query,
                    "currentFiles": list(request.current_files),
                    "activeDomains": list(request.active_domains),
                    "memory": {
                        "content": _normalized(entry.content, 2_000),
                        "scope": entry.scope.value,
                        "category": _normalized(entry.category, 128),
                        "tags": list(_bounded_strings(entry.tags)),
                        "domains": list(_bounded_strings(entry.domains)),
                        "metadata": _safe_metadata(entry.metadata),
                    },
                }
                for entry in batch
            ]
            saved_thinking = getattr(self._model, "_thinking_blocks", None)
            if saved_thinking is not None:
                self._model._thinking_blocks = []
            try:
                step = self._invoke_model(
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
                )
            finally:
                if saved_thinking is not None:
                    self._model._thinking_blocks = saved_thinking
            audits.extend(_parse_audits(step.content, [entry.id for entry in batch]))
            usage["model_call_count"] += 1
            step_usage = getattr(step, "usage", None)
            if step_usage is not None:
                usage["input_tokens"] += getattr(step_usage, "input_tokens", 0) or 0
                usage["output_tokens"] += getattr(step_usage, "output_tokens", 0) or 0
        by_id = {row["id"]: row for row in audits}
        if len(by_id) != len(audits) or len(by_id) != len(entries):
            raise ValueError("hybrid challenger emitted duplicate audits")
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
                    estimate_messages_tokens(messages)
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
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
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
        return step

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


def _parse_decisions(text: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    import re

    match = re.search(r"\{.*\}", str(text).strip(), re.S)
    if match is None:
        raise ValueError("hybrid verifier returned no JSON")
    payload = json.loads(match.group(0))
    rows = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        raise ValueError("hybrid verifier decision count mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
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
        ):
            raise ValueError("hybrid verifier decision is malformed")
        result.append(dict(row))
    if sorted(row["id"] for row in result) != sorted(expected_ids):
        raise ValueError("hybrid verifier IDs mismatch")
    return result


def _parse_audits(text: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    match = re.search(r"\{.*\}", str(text).strip(), re.S)
    if match is None:
        raise ValueError("hybrid challenger returned no JSON")
    payload = json.loads(match.group(0))
    rows = payload.get("audits") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        raise ValueError("hybrid challenger audit count mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("hybrid challenger audit is malformed")
        confidence = row.get("confidence")
        if (
            not isinstance(row.get("id"), str)
            or not isinstance(row.get("admit"), bool)
            or row.get("reasonCode") not in HYBRID_CHALLENGER_ALLOWED_REASONS
            or (row.get("admit") is True and row.get("reasonCode") != "no_disqualifier")
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


def _model_id(model_adapter: Any) -> str:
    direct = getattr(model_adapter, "model_id", "")
    if direct:
        return str(direct)
    runtime = getattr(model_adapter, "runtime", {})
    return str(runtime.get("model", "")) if isinstance(runtime, dict) else ""


def create_hybrid_candidate_provider(
    *,
    activation: HybridActivation,
    model_adapter: Any,
    workspace_path: str | Path | None = None,
    embedding_client_factory: Any | None = None,
) -> HybridRuntimeProvider | None:
    if not activation.active or activation.evidence is None:
        return None
    evidence = activation.evidence
    verifier = evidence.get("verifier")
    challenger = evidence.get("challenger")
    if not isinstance(verifier, dict) or not isinstance(challenger, dict):
        return None
    veto_reason_codes = challenger.get("veto_reason_codes")
    if not isinstance(veto_reason_codes, (list, tuple)):
        return None
    prompt_sha = _sha256_bytes(HYBRID_SYSTEM_PROMPT.encode("utf-8"))
    challenger_sha = _sha256_bytes(HYBRID_CHALLENGER_SYSTEM_PROMPT.encode("utf-8"))
    if (
        verifier.get("prompt_version") != HYBRID_PROMPT_VERSION
        or verifier.get("prompt_sha256") != prompt_sha
        or verifier.get("model_id") != _model_id(model_adapter)
        or challenger.get("prompt_version") != HYBRID_CHALLENGER_PROMPT_VERSION
        or challenger.get("prompt_sha256") != challenger_sha
        or challenger.get("model_id") != _model_id(model_adapter)
        or challenger.get("mode") != HYBRID_CHALLENGER_MODE
        or set(veto_reason_codes) != HYBRID_CHALLENGER_VETO_REASONS
    ):
        return None
    confidence = float(verifier.get("minimum_confidence", -1.0))
    challenger_confidence = float(challenger.get("minimum_confidence", -1.0))
    dense_top_k = int(evidence.get("dense_top_k", DEFAULT_DENSE_TOP_K))
    max_candidates = int(evidence.get("max_union_candidates", MAX_CANDIDATES))
    max_model_calls = int(
        evidence.get("max_model_calls_per_task", DEFAULT_MAX_MODEL_CALLS_PER_TASK)
    )
    if (
        not 0.0 <= confidence <= 1.0
        or not 0.0 <= challenger_confidence <= 1.0
        or not 1 <= dense_top_k <= DEFAULT_DENSE_TOP_K
        or not 1 <= max_candidates <= MAX_CANDIDATES
        or not 1 <= max_model_calls <= 16
    ):
        return None
    if activation.embedding_provider == "qwen":
        factory = (
            embedding_client_factory
            if embedding_client_factory is not None
            else create_openai_compatible_embedding_client
        )
        client = factory(workspace_path)
        if client is None:
            return None
        encoder: EmbeddingEncoder = OpenAICompatibleEmbeddingEncoder(
            client,
            provider="qwen",
        )
        if encoder.identity != evidence.get("model"):
            return None
    else:
        if activation.model_path is None:
            return None
        encoder = LocalE5Encoder(activation.model_path, evidence["model"])
    return HybridRuntimeProvider(
        encoder=encoder,
        model_adapter=model_adapter,
        dense_top_k=dense_top_k,
        max_candidates=max_candidates,
        minimum_confidence=confidence,
        challenger_minimum_confidence=challenger_confidence,
        max_model_calls=max_model_calls,
        embedding_provider=activation.embedding_provider,
    )
