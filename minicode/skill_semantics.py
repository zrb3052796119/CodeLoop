"""Semantic matching for the Skill routing layer.

Two matchers, one contract:

- :class:`AliasSemanticMatcher` — zero-dependency, always on. A curated
  bilingual concept table lets a Chinese query match English skill text and
  vice versa ("优化数据库查询" ↔ "database query performance tuning").
  Deterministic and instant, so it is the floor every install gets.

- :class:`EmbeddingSemanticMatcher` — optional cosine similarity over an
  OpenAI-compatible ``/embeddings`` endpoint (Qwen/DashScope compatible-mode
  and any compatible provider). Skill vectors are cached on disk keyed by
  ``content_digest``: unchanged skills cost no API calls, one query
  embedding per routing turn otherwise. Unavailable key, network error or
  disabled flag degrade silently to the alias floor.

Routing integration contract (see :mod:`minicode.skill_router`): the alias
floor may light a signal only through the strict unknown-intent gate, and
embedding contributions are threshold-gated so similarity can rank and
confirm, but never invent evidence on its own.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from minicode.embeddings import (
    DEFAULT_EMBEDDING_BASE_URL,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_API_KEY_ENV,
    EMBEDDING_BASE_URL_ENV,
    EMBEDDING_MODEL_ENV,
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingClient,
    cosine_similarity,
    create_openai_compatible_embedding_client,
    is_valid_embedding_vector,
    resolve_embedding_setting,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Concept alias table (the zero-dependency semantic floor)
# ---------------------------------------------------------------------------

# Each tuple is one routing concept; any member matches the whole group in
# both directions. Terms are normalized (lowercase, ASCII) before lookup.
_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("database", "sql", "数据库",),
    ("query", "查询",),
    ("optimize", "optimization", "performance", "tuning", "优化", "性能", "调优",),
    ("test", "testing", "pytest", "测试",),
    ("debug", "debugging", "troubleshoot", "调试", "排查",)
    ,
    ("refactor", "重构",),
    ("document", "documentation", "docs", "文档",),
    ("search", "搜索", "检索",),
    ("memory", "记忆",),
    ("skill", "技能",),
    ("routing", "路由",),
    ("agent", "代理",),
    ("frontend", "ui", "前端",),
    ("backend", "api", "接口", "后端",),
    ("security", "安全",),
    ("dependency", "package", "依赖", "包",),
    ("deploy", "deployment", "发布", "部署",),
    ("docker", "container", "容器",),
    ("git", "version control", "版本",),
    ("network", "网络",),
    ("error", "错误", "报错",),
    ("retry", "重试",),
    ("cache", "缓存",),
    ("config", "configuration", "配置",),
    ("file", "文件",),
    ("code", "代码",),
    ("review", "audit", "审查", "审核",),
    ("architecture", "架构",),
    ("log", "logging", "日志",),
    ("async", "concurrency", "并发", "异步",),
    ("migration", "迁移",),
    ("auth", "authentication", "认证", "鉴权",),
    ("report", "报表", "报告",),
    ("export", "导出",),
    ("parse", "parser", "解析",),
    ("schedule", "cron", "定时", "调度",),
    ("benchmark", "基准", "压测",),
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9_./-]+")


def _normalize_term(value: str) -> str:
    return str(value).strip().lower().replace("_", " ").replace("-", " ").strip()


def _semantic_tokens(text: str) -> set[str]:
    """ASCII words, whole CJK runs, and CJK bigrams as one token space."""
    lowered = str(text or "").lower()
    tokens = set(_WORD_RE.findall(lowered))
    for run in _CJK_RE.findall(lowered):
        tokens.add(run)
        tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    return {token for token in tokens if len(token) > 1 or not token.isascii()}


def _build_term_index() -> tuple[dict[str, tuple[int, ...]], tuple[frozenset[str], ...]]:
    term_to_groups: dict[str, list[int]] = {}
    groups: list[frozenset[str]] = []
    for index, group in enumerate(_CONCEPT_GROUPS):
        normalized = frozenset(
            term
            for raw in group
            for term in (_normalize_term(raw),)
            if term
        )
        groups.append(normalized)
        for term in normalized:
            term_to_groups.setdefault(term, []).append(index)
    return (
        {term: tuple(ids) for term, ids in term_to_groups.items()},
        tuple(groups),
    )


_TERM_TO_GROUPS, _GROUPS = _build_term_index()


class AliasSemanticMatcher:
    """Bilingual concept matching between a query and a skill's text."""

    def related_terms(self, term: str) -> set[str]:
        """Other members of the concept group containing ``term``."""
        normalized = _normalize_term(term)
        if not normalized:
            return set()
        related: set[str] = set()
        for group_id in _TERM_TO_GROUPS.get(normalized, ()):
            related.update(_GROUPS[group_id])
        related.discard(normalized)
        return related

    def matched_concepts(self, query_text: str, skill_text: str) -> set[str]:
        """Concepts whose members occur on both the query and the skill side."""
        query_tokens = _semantic_tokens(query_text)
        if not query_tokens:
            return set()
        skill_tokens = _semantic_tokens(skill_text)
        skill_flat = _normalize_term(skill_text)
        query_groups = {
            group_id
            for token in query_tokens
            for group_id in _TERM_TO_GROUPS.get(token, ())
        }
        # A CJK run inside the query may contain a concept as a substring
        # ("帮我优化数据库查询" contains "数据库") even though the run itself
        # is not a table term.
        query_flat = _normalize_term(query_text)
        for term, group_ids in _TERM_TO_GROUPS.items():
            if not term.isascii() and term in query_flat:
                query_groups.update(group_ids)
        if not query_groups:
            return set()

        matched: set[str] = set()
        for group_id in query_groups:
            for term in _GROUPS[group_id]:
                if term.isascii():
                    if term in skill_tokens:
                        matched.add(term)
                        break
                elif term in skill_flat:
                    matched.add(term)
                    break
        return matched

    def query_groups(self, query_text: str) -> set[int]:
        """Concept groups with any member present in the query text."""
        tokens = _semantic_tokens(query_text)
        flat = _normalize_term(query_text)
        groups: set[int] = set()
        for token in tokens:
            groups.update(_TERM_TO_GROUPS.get(token, ()))
        for term, group_ids in _TERM_TO_GROUPS.items():
            if not term.isascii() and term in flat:
                groups.update(group_ids)
        return groups

    def query_coverage(self, query_text: str, skill_text: str) -> float:
        """Share of the query's concept groups the skill also covers."""
        query_group_ids = self.query_groups(query_text)
        if not query_group_ids:
            return 0.0
        matched = self.matched_concepts(query_text, skill_text)
        if not matched:
            return 0.0
        matched_group_ids = {
            group_id
            for group_id, group in enumerate(_GROUPS)
            if group & matched
        }
        return min(1.0, len(matched_group_ids & query_group_ids) / len(query_group_ids))


# ---------------------------------------------------------------------------
# Embedding matcher (OpenAI-compatible / Qwen DashScope compatible-mode)
# ---------------------------------------------------------------------------

EMBEDDING_SIGNAL_THRESHOLD_ENV = "MINICODE_EMBEDDING_SIGNAL_THRESHOLD"
EMBEDDING_SIGNAL_THRESHOLD_UNKNOWN_ENV = (
    "MINICODE_EMBEDDING_SIGNAL_THRESHOLD_UNKNOWN"
)
EMBEDDING_BOOST_THRESHOLD_ENV = "MINICODE_EMBEDDING_BOOST_THRESHOLD"

# Defaults calibrated against Qwen text-embedding-v3 (DashScope
# compatible-mode): 6 relevant query/skill pairs scored 0.63-0.84 while the
# 24 unrelated pairs stayed <= 0.48. Mid-gap thresholds keep both sides
# clear. Override per provider via the env vars above.
DEFAULT_SIGNAL_THRESHOLD = 0.60
DEFAULT_SIGNAL_THRESHOLD_UNKNOWN = 0.67
DEFAULT_BOOST_THRESHOLD = 0.52

_MAX_BATCH = 24
_CACHE_MAX_ENTRIES = 2_000


class EmbeddingSemanticMatcher:
    """Cosine similarity with per-skill vectors cached by content digest.

    The cache lives at ``<workspace>/.mini-code/skill-embeddings.json``. A
    skill whose text (digest) did not change is never re-embedded, so the
    steady-state cost is one query embedding per routing turn.
    """

    def __init__(
        self,
        client: Any,
        cache_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._cache_path = Path(cache_path)
        self._clock = clock
        self._vectors: dict[str, list[float]] | None = None
        self._dirty = False

    @classmethod
    def from_environment(cls, workspace: str | Path) -> "EmbeddingSemanticMatcher | None":
        """Build a matcher from env vars or ``.env`` files, if configured.

        Resolution order per setting: process environment >
        ``~/.mini-code/.env`` > built-in default. Workspace files never own
        remote embedding credentials or endpoints. An empty API key keeps the
        matcher off entirely (alias matching still runs).
        """
        client = create_openai_compatible_embedding_client(workspace)
        if client is None:
            return None
        root = Path(workspace) / ".mini-code"
        return cls(client, root / "skill-embeddings.json")

    # ── Cache ───────────────────────────────────────────────────────

    def _cache_identity(self) -> tuple[str, str]:
        return (
            str(getattr(self._client, "_model", "")),
            str(getattr(self._client, "_endpoint", "")),
        )

    @property
    def circuit_identity(self) -> tuple[str, str, str]:
        """Non-secret workspace/provider identity for the failure circuit."""
        model, endpoint = self._cache_identity()
        return str(self._cache_path.parent.resolve()), model, endpoint

    def _load_cache(self) -> dict[str, list[float]]:
        if self._vectors is not None:
            return self._vectors
        try:
            with open(self._cache_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            vectors = data.get("vectors") if isinstance(data, dict) else None
            cached_identity = (
                str(data.get("model", "")) if isinstance(data, dict) else "",
                str(data.get("endpoint", "")) if isinstance(data, dict) else "",
            )
            if cached_identity != self._cache_identity():
                self._vectors = {}
                return self._vectors
            self._vectors = (
                {
                    str(k): [float(item) for item in v]
                    for k, v in vectors.items()
                    if is_valid_embedding_vector(v)
                }
                if isinstance(vectors, dict)
                else {}
            )
        except (OSError, ValueError):
            self._vectors = {}
        return self._vectors

    def _save_cache(self) -> None:
        if not self._dirty or self._vectors is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            trimmed = dict(
                sorted(self._vectors.items(), key=lambda item: item[0])[
                    -_CACHE_MAX_ENTRIES:
                ]
            )
            descriptor, temporary = tempfile.mkstemp(
                dir=str(self._cache_path.parent),
                prefix=".skill-embeddings-",
                suffix=".tmp",
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {"model": self._cache_identity()[0],
                     "endpoint": self._cache_identity()[1],
                     "updated": self._clock(),
                     "vectors": trimmed},
                    handle,
                )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._cache_path)
            self._dirty = False
        except OSError as error:
            logger.warning("Skill embedding cache write failed: %s", error)

    # ── Matching ────────────────────────────────────────────────────

    def similarities(
        self,
        query: str,
        skills: Sequence[tuple[str, str]],
    ) -> list[float]:
        """Cosine similarity of ``query`` against each ``(digest, text)``.

        Raises :class:`EmbeddingUnavailable` only when the query embedding
        itself cannot be fetched; per-skill failures simply yield 0.0 so one
        bad skill never takes routing down.
        """
        cache = self._load_cache()
        missing: list[tuple[str, str]] = []
        seen: set[str] = set()
        for digest, text in skills:
            key = _digest_key(digest)
            if text.strip() and key not in cache and key not in seen:
                seen.add(key)
                missing.append((key, text))
        if missing:
            # Fetch every uncached skill, but keep each request chain bounded:
            # the outer client already splits at the endpoint batch limit, and
            # this second cap avoids one giant uninterrupted call sequence for
            # a very large catalog while still filling every missing vector.
            try:
                for start in range(0, len(missing), _MAX_BATCH):
                    batch = missing[start : start + _MAX_BATCH]
                    vectors = self._client.embed(
                        [text for _, text in batch]
                    )
                    for (key, _), vector in zip(batch, vectors):
                        if is_valid_embedding_vector(vector):
                            cache[key] = [float(item) for item in vector]
                            self._dirty = True
                if len(missing) > _MAX_BATCH:
                    logger.info(
                        "Skill embedding cache filled in %d batches",
                        (len(missing) + _MAX_BATCH - 1) // _MAX_BATCH,
                    )
            except EmbeddingUnavailable as error:
                self._save_cache()
                logger.info("Skill embedding fetch skipped: %s", error)

        try:
            query_vector = self._client.embed_one(query)
        except EmbeddingUnavailable:
            self._save_cache()
            raise

        results: list[float] = []
        for digest, text in skills:
            if not text.strip():
                results.append(0.0)
                continue
            vector = cache.get(_digest_key(digest))
            results.append(
                cosine_similarity(query_vector, vector) if vector else 0.0
            )
        self._save_cache()
        return results


def embedding_thresholds(
    workspace: str | Path | None = None,
) -> tuple[float, float, float]:
    """(signal, signal_unknown, boost) similarity thresholds.

    Overridable per provider: cosine distributions differ substantially
    between embedding models, so the calibrated Qwen defaults are just a
    starting point exposed through the embedding env vars.
    """
    def as_float(name: str, default: float) -> float:
        try:
            return float(
                resolve_embedding_setting(workspace, name, str(default)) or default
            )
        except ValueError:
            return default

    return (
        as_float(EMBEDDING_SIGNAL_THRESHOLD_ENV, DEFAULT_SIGNAL_THRESHOLD),
        as_float(
            EMBEDDING_SIGNAL_THRESHOLD_UNKNOWN_ENV, DEFAULT_SIGNAL_THRESHOLD_UNKNOWN
        ),
        as_float(EMBEDDING_BOOST_THRESHOLD_ENV, DEFAULT_BOOST_THRESHOLD),
    )


def _digest_key(digest: str) -> str:
    return str(digest)[:64]


__all__ = [
    "AliasSemanticMatcher",
    "EmbeddingSemanticMatcher",
    "EmbeddingUnavailable",
    "OpenAICompatibleEmbeddingClient",
    "cosine_similarity",
    "DEFAULT_EMBEDDING_BASE_URL",
    "DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_API_KEY_ENV",
    "EMBEDDING_BASE_URL_ENV",
    "EMBEDDING_MODEL_ENV",
]
