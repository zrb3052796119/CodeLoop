"""Safe representation and temporary embedding index for Retrieval Phase 3B."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from experiments.memory_embedding_adapter import EmbeddingAdapter, Vector, validate_vectors


INDEX_SCHEMA_VERSION = "1.0"
MAX_CONTENT_CHARS = 4000
MAX_LIST_ITEMS = 32
MAX_ITEM_CHARS = 512
_SAFE_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$", re.I)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?im)^\s*(authorization|password|token|secret|api[_ -]?key)\s*[:=]\s*[^\r\n]+"),
)
_INJECTION_RE = re.compile(
    r"(?:ignore|override|disregard).{0,40}(?:system|developer|instruction)|"
    r"(?:reveal|print|output|exfiltrate).{0,40}(?:secret|token|environment|credential)|"
    r"(?:忽略|绕过|覆盖).{0,20}(?:系统|开发者|指令)|"
    r"(?:输出|泄露|打印).{0,20}(?:环境变量|密钥|令牌|凭据)",
    re.I | re.S,
)
_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])([A-Za-z0-9+/]{24,}={0,2})(?![A-Za-z0-9+/])")
_CONFUSABLES = str.maketrans(
    {
        "і": "i",
        "Ι": "I",
        "ο": "o",
        "о": "o",
        "ѕ": "s",
        "у": "y",
        "е": "e",
        "а": "a",
        "р": "p",
        "с": "c",
        "х": "x",
    }
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_text(value: Any, *, max_chars: int = MAX_ITEM_CHARS) -> str:
    if not isinstance(value, str):
        raise ValueError("allowlisted representation values must be strings")
    return " ".join(unicodedata.normalize("NFKC", value).split())[:max_chars]


def _bounded_strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_LIST_ITEMS:
        raise ValueError("allowlisted metadata must be a bounded string list")
    result = tuple(item for item in (_normalized_text(item) for item in value) if item)
    return tuple(dict.fromkeys(result))


def _contains_injection(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).translate(_CONFUSABLES)
    if _INJECTION_RE.search(normalized):
        return True
    for match in _BASE64_RE.finditer(normalized):
        try:
            decoded = (
                base64.b64decode(match.group(1), validate=True)
                .decode("utf-8", "ignore")
                .translate(_CONFUSABLES)
            )
        except (ValueError, UnicodeError):
            continue
        if _INJECTION_RE.search(decoded):
            return True
    return False


def redact_sensitive_text(text: str) -> tuple[str, tuple[str, ...]]:
    normalized = unicodedata.normalize("NFKC", text)
    reasons: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(normalized):
            normalized = pattern.sub("[REDACTED]", normalized)
            reasons.append("secret_redacted")
    return normalized, tuple(dict.fromkeys(reasons))


def eligibility_reason(entry: dict[str, Any], visible_scopes: set[str] | None = None) -> str:
    if entry.get("approval_status") != "approved":
        return "approval_not_approved"
    if entry.get("lifecycle_status") != "active":
        return "lifecycle_not_active"
    if entry.get("safety_status") != "safe":
        return "safety_not_safe"
    if bool(entry.get("curator_locked")):
        return "curator_locked"
    if entry.get("tier") == "archival":
        return "archival_tier"
    scope = entry.get("scope")
    if scope not in {"user", "project", "local"}:
        return "invalid_scope"
    if visible_scopes is not None and scope not in visible_scopes:
        return "scope_not_visible"
    content = entry.get("content")
    if not isinstance(content, str) or not content.strip():
        return "empty_content"
    if len(content) > MAX_CONTENT_CHARS:
        return "oversized_content"
    if _contains_injection(content):
        return "instruction_intent_detected"
    return "eligible"


def document_representation(entry: dict[str, Any], representation_version: str) -> str:
    reason = eligibility_reason(entry)
    if reason != "eligible":
        raise ValueError(f"entry is not embedding-eligible: {reason}")
    content, _ = redact_sensitive_text(_normalized_text(entry["content"], max_chars=MAX_CONTENT_CHARS))
    if representation_version == "content-v1":
        return content
    if representation_version != "structured-v1":
        raise ValueError("unsupported representation version")
    category = _normalized_text(entry.get("category", ""))
    tags = _bounded_strings(entry.get("tags", []))
    domains = _bounded_strings(entry.get("domains", []))
    metadata = entry.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    file_paths: list[str] = []
    for key in ("files", "file_paths", "paths"):
        if key in metadata:
            file_paths.extend(_bounded_strings(metadata[key]))
    source = _normalized_text(entry.get("source", ""), max_chars=64)
    source_type = source if _SAFE_SOURCE_RE.fullmatch(source) else "unspecified"
    parts = [f"content: {content}"]
    if category:
        parts.append(f"category: {category}")
    if tags:
        parts.append(f"tags: {' | '.join(tags)}")
    if domains:
        parts.append(f"domains: {' | '.join(domains)}")
    if file_paths:
        parts.append(f"files: {' | '.join(dict.fromkeys(file_paths))}")
    if source_type != "unspecified":
        parts.append(f"source_type: {source_type}")
    return "\n".join(parts)


def query_representation(
    query: str,
    *,
    current_files: Sequence[str] = (),
    active_domains: Sequence[str] = (),
    outcome_context: str = "",
) -> str:
    query = _normalized_text(query, max_chars=2000)
    if not query:
        raise ValueError("queryless retrieval fails closed")
    parts = [f"task: {query}"]
    files = _bounded_strings(current_files)
    domains = _bounded_strings(active_domains)
    if files:
        parts.append(f"current_files: {' | '.join(files)}")
    if domains:
        parts.append(f"active_domains: {' | '.join(domains)}")
    if outcome_context:
        safe_outcome, _ = redact_sensitive_text(_normalized_text(outcome_context, max_chars=240))
        if not _contains_injection(safe_outcome):
            parts.append(f"outcome: {safe_outcome}")
    return "\n".join(parts)


def approval_hash(entry: dict[str, Any]) -> str:
    fields = {
        "approval_status": entry.get("approval_status"),
        "lifecycle_status": entry.get("lifecycle_status"),
        "safety_status": entry.get("safety_status"),
        "curator_locked": bool(entry.get("curator_locked")),
        "tier": entry.get("tier"),
        "scope": entry.get("scope"),
    }
    return _sha256_text(_stable_json(fields))


@dataclass(frozen=True)
class EmbeddingIndexRecord:
    entry_id: str
    scope: str
    content_hash: str
    approval_hash: str
    model_id: str
    model_revision: str
    model_fingerprint: str
    representation_version: str
    representation_hash: str
    embedding_dimension: int
    representation: str
    vector: Vector


class EmbeddingIndex:
    """In-memory index with an optional atomic JSON cache in a temporary directory."""

    def __init__(
        self,
        adapter: EmbeddingAdapter,
        *,
        representation_version: str,
        cache_root: Path | None = None,
    ) -> None:
        if representation_version not in {"content-v1", "structured-v1"}:
            raise ValueError("unsupported representation version")
        self.adapter = adapter
        self.representation_version = representation_version
        self._records: dict[str, EmbeddingIndexRecord] = {}
        self._lock = threading.RLock()
        self._matrix_ids: tuple[str, ...] = ()
        self._matrix: Any | None = None
        self._cache_root = self._validate_cache_root(cache_root) if cache_root else None
        self.last_cache_error = ""

    @staticmethod
    def _validate_cache_root(root: Path) -> Path:
        candidate = Path(root).expanduser()
        if ".." in candidate.parts:
            raise ValueError("cache traversal is forbidden")
        candidate.mkdir(parents=True, exist_ok=True)
        if candidate.is_symlink():
            raise ValueError("cache root cannot be a symlink")
        resolved = candidate.resolve(strict=True)
        return resolved

    @property
    def cache_path(self) -> Path | None:
        return self._cache_root / "embedding-index.json" if self._cache_root else None

    @property
    def records(self) -> tuple[EmbeddingIndexRecord, ...]:
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    @property
    def index_bytes(self) -> int:
        path = self.cache_path
        return path.stat().st_size if path and path.is_file() else 0

    @property
    def cache_key(self) -> str:
        fields = {
            "schema": INDEX_SCHEMA_VERSION,
            "model_id": self.adapter.model_id,
            "model_revision": self.adapter.model_revision,
            "model_fingerprint": self.adapter.model_fingerprint,
            "dimension": self.adapter.embedding_dimension,
            "representation_version": self.representation_version,
        }
        return _sha256_text(_stable_json(fields))

    def build(self, entries: Iterable[dict[str, Any]], *, visible_scopes: set[str]) -> dict[str, int]:
        specs = list(entries)
        ids = [str(item.get("id", "")) for item in specs]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("entry IDs must be non-empty and unique")
        eligible: list[tuple[dict[str, Any], str]] = []
        skipped = 0
        for entry in specs:
            if eligibility_reason(entry, visible_scopes) != "eligible":
                skipped += 1
                continue
            eligible.append((entry, document_representation(entry, self.representation_version)))
        vectors = self.adapter.encode_documents([item[1] for item in eligible]) if eligible else ()
        records = {
            str(entry["id"]): self._record(entry, representation, vector)
            for (entry, representation), vector in zip(eligible, vectors, strict=True)
        }
        with self._lock:
            self._records = records
            self._invalidate_search_cache()
        return {"indexed": len(records), "skipped": skipped}

    def _invalidate_search_cache(self) -> None:
        self._matrix_ids = ()
        self._matrix = None

    def _record(
        self, entry: dict[str, Any], representation: str, vector: Sequence[float]
    ) -> EmbeddingIndexRecord:
        validated = validate_vectors(
            [vector],
            expected_count=1,
            expected_dimension=self.adapter.embedding_dimension,
            require_normalized=self.adapter.normalize,
        )[0]
        return EmbeddingIndexRecord(
            entry_id=str(entry["id"]),
            scope=str(entry["scope"]),
            content_hash=_sha256_text(str(entry["content"])),
            approval_hash=approval_hash(entry),
            model_id=self.adapter.model_id,
            model_revision=self.adapter.model_revision,
            model_fingerprint=self.adapter.model_fingerprint,
            representation_version=self.representation_version,
            representation_hash=_sha256_text(representation),
            embedding_dimension=self.adapter.embedding_dimension,
            representation=representation,
            vector=validated,
        )

    def upsert(self, entry: dict[str, Any], *, visible_scopes: set[str]) -> str:
        entry_id = str(entry.get("id", ""))
        if not entry_id:
            raise ValueError("entry ID is required")
        reason = eligibility_reason(entry, visible_scopes)
        if reason != "eligible":
            self.delete(entry_id)
            return f"removed:{reason}"
        representation = document_representation(entry, self.representation_version)
        expected = (
            _sha256_text(str(entry["content"])),
            approval_hash(entry),
            _sha256_text(representation),
        )
        with self._lock:
            current = self._records.get(entry_id)
            if current and (
                current.content_hash,
                current.approval_hash,
                current.representation_hash,
            ) == expected:
                return "unchanged"
        vector = self.adapter.encode_documents([representation])[0]
        with self._lock:
            self._records[entry_id] = self._record(entry, representation, vector)
            self._invalidate_search_cache()
        return "updated" if current else "inserted"

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            removed = self._records.pop(entry_id, None) is not None
            if removed:
                self._invalidate_search_cache()
            return removed

    def delete_scope(self, scope: str) -> int:
        with self._lock:
            doomed = [key for key, record in self._records.items() if record.scope == scope]
            for key in doomed:
                del self._records[key]
            if doomed:
                self._invalidate_search_cache()
        return len(doomed)

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        allowed_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        if limit <= 0:
            return []
        vector = validate_vectors(
            [query_vector],
            expected_count=1,
            expected_dimension=self.adapter.embedding_dimension,
            require_normalized=self.adapter.normalize,
        )[0]
        try:
            import numpy as np
        except ImportError:
            np = None
        if np is not None:
            with self._lock:
                if self._matrix is None:
                    ordered = tuple(self._records[key] for key in sorted(self._records))
                    self._matrix_ids = tuple(record.entry_id for record in ordered)
                    self._matrix = np.asarray(
                        [record.vector for record in ordered], dtype=np.float32
                    )
                ids = self._matrix_ids
                matrix = self._matrix
            indices = [
                index
                for index, entry_id in enumerate(ids)
                if allowed_ids is None or entry_id in allowed_ids
            ]
            if not indices:
                return []
            query = np.asarray(vector, dtype=np.float32)
            values = matrix[indices] @ query
            scores = [(ids[index], float(score)) for index, score in zip(indices, values, strict=True)]
            scores.sort(key=lambda item: (-item[1], item[0]))
            return scores[:limit]
        with self._lock:
            records = tuple(self._records.values())
        scores = [
            (record.entry_id, sum(left * right for left, right in zip(vector, record.vector, strict=True)))
            for record in records
            if allowed_ids is None or record.entry_id in allowed_ids
        ]
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:limit]

    def save(self) -> None:
        path = self.cache_path
        if path is None:
            raise ValueError("cache_root was not configured")
        with self._lock:
            payload = {
                "schema_version": INDEX_SCHEMA_VERSION,
                "cache_key": self.cache_key,
                "records": [
                    {**asdict(record), "vector": list(record.vector)} for record in self.records
                ],
            }
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def load(self) -> bool:
        path = self.cache_path
        if path is None or not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != INDEX_SCHEMA_VERSION:
                raise ValueError("unsupported index schema")
            if payload.get("cache_key") != self.cache_key:
                raise ValueError("stale model or representation cache")
            raw_records = payload.get("records")
            if not isinstance(raw_records, list):
                raise ValueError("index records are malformed")
            loaded: dict[str, EmbeddingIndexRecord] = {}
            for raw in raw_records:
                if not isinstance(raw, dict):
                    raise ValueError("index record is malformed")
                vector = validate_vectors(
                    [raw.get("vector", [])],
                    expected_count=1,
                    expected_dimension=self.adapter.embedding_dimension,
                    require_normalized=self.adapter.normalize,
                )[0]
                record = EmbeddingIndexRecord(**{**raw, "vector": vector})
                if record.entry_id in loaded:
                    raise ValueError("duplicate entry ID in index")
                if record.representation_hash != _sha256_text(record.representation):
                    raise ValueError("content hash mismatch in index")
                if (
                    record.model_id != self.adapter.model_id
                    or record.model_revision != self.adapter.model_revision
                    or record.model_fingerprint != self.adapter.model_fingerprint
                    or record.representation_version != self.representation_version
                ):
                    raise ValueError("stale cache record")
                loaded[record.entry_id] = record
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.last_cache_error = str(exc)
            with self._lock:
                self._records = {}
                self._invalidate_search_cache()
            return False
        with self._lock:
            self._records = loaded
            self._invalidate_search_cache()
        self.last_cache_error = ""
        return True
