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

import inspect
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
    ("optimize", "optimization", "tuning", "优化", "调优",),
    ("performance", "slow", "slowness", "性能", "慢", "卡顿", "开销",),
    (
        "profile", "profiling", "hotspot", "测量", "瓶颈", "热点", "性能剖析",
    ),
    ("timing", "时间",),
    (
        "cpu", "latency", "throughput", "lock contention", "p95",
        "regression", "regressions", "runtime", "application runtime",
        "耗时", "延迟", "吞吐", "锁等待", "回归", "运行时",
    ),
    ("test", "testing", "pytest", "测试",),
    ("fixture", "test fixture", "夹具",),
    ("debug", "debugging", "troubleshoot", "diagnose", "root cause", "调试", "排查", "根因",),
    ("refactor", "重构",),
    (
        "document", "documentation", "docs", "readme", "guide", "manual",
        "adr", "rfc", "spec", "specification", "文档", "指南", "手册", "规范",
    ),
    (
        "poem", "poetry", "verse", "诗", "诗歌", "短诗",
    ),
    ("explain", "explanation", "describe", "understand", "说明", "解释", "讲解",),
    (
        "call flow", "call path", "call chain", "execution path", "data flow",
        "state flow", "component hierarchy", "path", "调用链", "调用路径", "执行路径",
        "数据流", "状态流", "组件层级", "链路", "路径",
    ),
    ("search", "搜索", "检索",),
    (
        "news", "headline", "headlines", "breaking news", "新闻", "热点新闻",
        "头条",
    ),
    ("definition", "defined", "reference", "references", "usage", "定义", "引用", "调用点",),
    ("memory", "记忆",),
    (
        "persistent memory", "long term memory", "agent memory",
        "长期记忆", "持久记忆",
    ),
    ("retrieval", "recall", "召回", "检索",),
    ("provenance", "source chain", "来源", "来源链",),
    ("preference", "偏好",),
    ("context", "context window", "上下文",),
    ("compaction", "compression", "压缩",),
    ("summary", "fidelity", "摘要", "保真",),
    ("skill", "技能",),
    ("routing", "路由",),
    ("agent", "multi agent", "sub agent", "worker", "工作单元", "代理", "子代理",),
    ("delegation", "task assignment", "parallel agent", "aggregation", "委派", "分工", "任务分配", "汇总",),
    ("frontend", "ui", "前端",),
    (
        "component", "layout", "responsive", "mobile", "overflow",
        "组件", "布局", "移动端", "手机", "溢出",
    ),
    ("backend", "api", "接口", "后端",),
    (
        "security", "credential", "credentials", "secret", "localstorage",
        "local storage", "安全", "凭据", "密钥",
    ),
    (
        "injection", "command injection", "shell injection", "sql injection",
        "命令注入", "代码注入", "sql注入", "sql 注入",
    ),
    (
        "permission", "permissions", "authorization", "access control",
        "unauthorized", "admin access", "权限", "授权", "越权", "管理员权限",
    ),
    ("dependency", "package", "依赖", "包",),
    (
        "deploy", "deployment", "rollout", "rollback", "pipeline",
        "image", "gray release", "发布", "部署", "灰度", "回滚", "流水线", "镜像", "放量", "上线",
    ),
    ("docker", "container", "容器",),
    (
        "git", "version control", "branch", "commit", "rebase", "merge conflict",
        "history", "rollback record", "分支", "提交", "变基", "合并冲突", "开发线", "版本记录", "可回滚记录",
    ),
    ("injection", "注入", "指令执行", "命令执行", "当成指令",),
    ("path traversal", "traversal", "目录穿越", "绕出", "绕过", "越权访问",),
    ("network", "网络",),
    ("error", "错误", "报错",),
    ("retry", "重试",),
    ("cache", "缓存",),
    ("config", "configuration", "配置",),
    ("file", "文件",),
    ("module", "模块",),
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


def _contains_semantic_term(
    flat_text: str,
    tokens: set[str],
    term: str,
) -> bool:
    """Match one normalized alias, including multi-word/qualified phrases."""
    if not term:
        return False
    if not term.isascii():
        return term in flat_text
    if term in tokens:
        return True
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            flat_text,
        )
        is not None
    )


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
        query_flat = _normalize_term(query_text)
        if not query_tokens and not query_flat:
            return set()
        skill_tokens = _semantic_tokens(skill_text)
        skill_flat = _normalize_term(skill_text)
        query_groups = {
            group_id
            for term, group_ids in _TERM_TO_GROUPS.items()
            if _contains_semantic_term(query_flat, query_tokens, term)
            for group_id in group_ids
        }
        if not query_groups:
            return set()

        matched: set[str] = set()
        for group_id in query_groups:
            for term in _GROUPS[group_id]:
                if _contains_semantic_term(skill_flat, skill_tokens, term):
                    matched.add(term)
                    break
        return matched

    def matched_group_ids(self, query_text: str, skill_text: str) -> set[int]:
        """Stable concept identities shared by query and Skill text."""
        matched = self.matched_concepts(query_text, skill_text)
        return {
            group_id
            for term in matched
            for group_id in _TERM_TO_GROUPS.get(term, ())
        }

    def query_groups(self, query_text: str) -> set[int]:
        """Concept groups with any member present in the query text."""
        tokens = _semantic_tokens(query_text)
        flat = _normalize_term(query_text)
        return {
            group_id
            for term, group_ids in _TERM_TO_GROUPS.items()
            if _contains_semantic_term(flat, tokens, term)
            for group_id in group_ids
        }

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
_MAX_NEW_VECTORS_PER_ROUTE = 24
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
        cache_anchor: str | Path | None = None,
    ) -> None:
        self._client = client
        self._cache_path = Path(cache_path)
        self._clock = clock
        self._cache_anchor = (
            Path(cache_anchor).absolute() if cache_anchor is not None else None
        )
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
        try:
            client = create_openai_compatible_embedding_client(workspace)
        except EmbeddingUnavailable as error:
            logger.warning(
                "Skill embedding configuration is unavailable; alias matching "
                "only: %s",
                error,
            )
            return None
        if client is None:
            return None
        root = Path(workspace) / ".mini-code"
        return cls(
            client,
            root / "skill-embeddings.json",
            cache_anchor=workspace,
        )

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
        if not self._cache_path_is_safe():
            logger.warning(
                "Skill embedding cache ignored because its managed path is unsafe"
            )
            self._vectors = {}
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
        if not self._cache_path_is_safe():
            logger.warning(
                "Skill embedding cache write skipped because its managed path is unsafe"
            )
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self._cache_path_is_safe():
                logger.warning(
                    "Skill embedding cache write skipped after an unsafe path change"
                )
                return
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

    def _cache_path_is_safe(self) -> bool:
        """Reject workspace-managed cache roots redirected through symlinks."""
        anchor = self._cache_anchor
        if anchor is None:
            return True
        try:
            cache_path = self._cache_path.absolute()
            relative = cache_path.relative_to(anchor)
            if anchor.is_symlink():
                return False
            current = anchor
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    return False

            resolved_anchor = anchor.resolve(strict=True)
            existing_parent = cache_path.parent
            while not existing_parent.exists():
                parent = existing_parent.parent
                if parent == existing_parent:
                    return False
                existing_parent = parent
            return existing_parent.resolve(strict=True).is_relative_to(
                resolved_anchor
            )
        except (OSError, RuntimeError, ValueError):
            return False

    # ── Matching ────────────────────────────────────────────────────

    def similarities(
        self,
        query: str,
        skills: Sequence[tuple[str, str]],
        *,
        call_context: dict[str, Any] | None = None,
        max_new_vectors: int | None = None,
    ) -> list[float]:
        """Cosine similarity of ``query`` against each ``(digest, text)``.

        Provider failures raise :class:`EmbeddingUnavailable` so the router's
        shared cooldown can prevent repeated document-batch retries. Invalid
        individual vectors still yield 0.0 for only that Skill.
        ``max_new_vectors`` overrides the per-turn document warm-up cap for
        callers whose vectors are few by construction (directory presel).
        """
        cache = self._load_cache()
        missing: list[tuple[str, str]] = []
        seen: set[str] = set()
        for digest, text in skills:
            key = _digest_key(digest)
            if text.strip() and key not in cache and key not in seen:
                seen.add(key)
                missing.append((key, text))
        cap = _MAX_NEW_VECTORS_PER_ROUTE if max_new_vectors is None else max(
            1, int(max_new_vectors)
        )
        pending = missing[:cap]
        if pending:
            # Warm a bounded prefix on each turn. Large catalogs converge over
            # subsequent turns without allowing cold-cache routing to issue an
            # unbounded remote request chain before the Agent loop starts.
            try:
                for start in range(0, len(pending), _MAX_BATCH):
                    batch = pending[start : start + _MAX_BATCH]
                    vectors = _call_with_optional_context(
                        self._client.embed,
                        [text for _, text in batch],
                        call_context=call_context,
                    )
                    for (key, _), vector in zip(batch, vectors):
                        if is_valid_embedding_vector(vector):
                            cache[key] = [float(item) for item in vector]
                            self._dirty = True
                if len(pending) > _MAX_BATCH:
                    logger.info(
                        "Skill embedding cache filled in %d batches",
                        (len(pending) + _MAX_BATCH - 1) // _MAX_BATCH,
                    )
            except EmbeddingUnavailable as error:
                self._save_cache()
                logger.info("Skill embedding fetch failed: %s", error)
                raise
        if len(missing) > len(pending):
            logger.info(
                "Skill embedding warm-up deferred %d vectors to later turns",
                len(missing) - len(pending),
            )

        try:
            query_vector = _call_with_optional_context(
                self._client.embed_one,
                query,
                call_context=call_context,
            )
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


def _call_with_optional_context(
    method: Callable[..., Any],
    *args: Any,
    call_context: dict[str, Any] | None,
) -> Any:
    """Pass call context only to clients whose public signature accepts it."""
    if call_context is None:
        return method(*args)
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    if any(
        parameter.name == "call_context"
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    ):
        return method(*args, call_context=call_context)
    return method(*args)


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
