"""Layered memory system for cross-session knowledge retention.

Provides three-tier memory hierarchy:
- User memory (~/.mini-code/memory/) - cross-project, persistent
- Project memory (.mini-code-memory/) - shared across sessions, can be versioned
- Local memory (.mini-code-memory-local/) - project-specific, not checked in

Memory is automatically injected into system prompts to give the agent
context about past decisions, codebase patterns, and project conventions.

Search uses TF-IDF relevance scoring for intelligent retrieval.
"""

from __future__ import annotations

import functools
import base64
import binascii
import hashlib
import json
import logging
import math
import os
import re
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from minicode.config import MINI_CODE_DIR
from minicode.memory_store import (
    MemoryStoreConflict,
    MemoryStoreCoordinator,
    MemoryStoreUnsafePath,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Memory data validation
# ---------------------------------------------------------------------------


def _validate_memory_data(data: dict) -> tuple[bool, list[str]]:
    """Validate the structure of memory JSON data before loading.

    Checks for:
    - Required fields present (entries)
    - Valid enum values for scope
    - Valid data types for all entry fields

    Args:
        data: Parsed JSON data dictionary

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Root data must be a dictionary"]

    if "entries" not in data:
        errors.append("Missing required field: 'entries'")
        return False, errors

    entries = data.get("entries")
    if not isinstance(entries, list):
        errors.append("'entries' must be a list")
        return False, errors

    for idx, entry_data in enumerate(entries):
        _, entry_errors = _validate_entry(entry_data, idx)
        errors.extend(entry_errors)

    return len(errors) == 0, errors


def _validate_entry(entry: Any, index: int) -> tuple[bool, list[str]]:
    """Validate a single memory entry dictionary.

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors: list[str] = []
    prefix = f"Entry at index {index}"

    if not isinstance(entry, dict):
        return False, [f"{prefix} is not a dictionary"]

    required_fields = ["id", "content"]
    for field_name in required_fields:
        if field_name not in entry:
            errors.append(f"{prefix} missing required field: '{field_name}'")

    if "id" in entry and not isinstance(entry["id"], str):
        errors.append(f"{prefix} field 'id' must be a string")

    if "scope" in entry:
        scope_val = entry["scope"]
        if not isinstance(scope_val, str):
            errors.append(f"{prefix} field 'scope' must be a string")
        elif scope_val not in _VALID_SCOPES:
            errors.append(
                f"{prefix} has invalid scope value: '{scope_val}'. "
                f"Must be one of: {', '.join(sorted(_VALID_SCOPES))}"
            )

    if "category" in entry and not isinstance(entry["category"], str):
        errors.append(f"{prefix} field 'category' must be a string")

    if "content" in entry and not isinstance(entry["content"], str):
        errors.append(f"{prefix} field 'content' must be a string")

    if "created_at" in entry:
        val = entry["created_at"]
        if not isinstance(val, (int, float)):
            errors.append(f"{prefix} field 'created_at' must be a number")

    if "updated_at" in entry:
        val = entry["updated_at"]
        if not isinstance(val, (int, float)):
            errors.append(f"{prefix} field 'updated_at' must be a number")

    if "tags" in entry:
        val = entry["tags"]
        if not isinstance(val, list):
            errors.append(f"{prefix} field 'tags' must be a list")
        elif not all(isinstance(t, str) for t in val):
            errors.append(f"{prefix} field 'tags' must contain only strings")

    if "usage_count" in entry:
        val = entry["usage_count"]
        if not isinstance(val, int):
            errors.append(f"{prefix} field 'usage_count' must be an integer")

    for list_field in ("domains", "related_to"):
        if list_field in entry:
            val = entry[list_field]
            if not isinstance(val, list):
                errors.append(f"{prefix} field '{list_field}' must be a list")
            elif not all(isinstance(t, str) for t in val):
                errors.append(
                    f"{prefix} field '{list_field}' must contain only strings"
                )

    for int_field in (
        "retrieval_count",
        "injection_count",
        "success_count",
        "failure_count",
        "corroborated_success_count",
        "corroborated_failure_count",
    ):
        if int_field in entry and not isinstance(entry[int_field], int):
            errors.append(f"{prefix} field '{int_field}' must be an integer")

    for float_field in (
        "last_accessed",
        "last_used",
        "usefulness_score",
        "corroborated_usefulness_score",
    ):
        if float_field in entry and not isinstance(entry[float_field], (int, float)):
            errors.append(f"{prefix} field '{float_field}' must be a number")

    for dict_field in ("metadata", "provenance"):
        if dict_field in entry and not isinstance(entry[dict_field], dict):
            errors.append(f"{prefix} field '{dict_field}' must be a dictionary")

    for str_field in (
        "tier",
        "source",
        "lifecycle_status",
        "tier_reason",
        "safety_status",
        "safety_reason",
        "approval_status",
        "approval_content_hash",
        "approval_reason",
        "approval_actor",
        "approval_policy",
    ):
        if str_field in entry and not isinstance(entry[str_field], str):
            errors.append(f"{prefix} field '{str_field}' must be a string")

    for nullable_float_field in ("deprecated_at", "approval_decided_at"):
        if entry.get(nullable_float_field) is not None:
            if not isinstance(entry[nullable_float_field], (int, float)):
                errors.append(
                    f"{prefix} field '{nullable_float_field}' must be a number or null"
                )

    if "curator_locked" in entry and not isinstance(entry["curator_locked"], bool):
        errors.append(f"{prefix} field 'curator_locked' must be a boolean")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Corrupted data recovery
# ---------------------------------------------------------------------------

def _recover_entries(data: dict, memory_json_path: Path) -> list[dict]:
    """Attempt to recover valid entries from corrupted memory data.

    Creates a backup of the corrupted file and returns only valid entries.

    Args:
        data: Parsed JSON data (may be partially corrupted)
        memory_json_path: Path to the original memory.json file

    Returns:
        List of valid entry dictionaries
    """
    backup_path = memory_json_path.with_suffix(".json.bak")
    try:
        import shutil
        shutil.copy2(str(memory_json_path), str(backup_path))
        logger.warning(
            "Corrupted memory file backed up to %s", backup_path
        )
    except OSError as e:
        logger.error(
            "Failed to create backup of corrupted memory file: %s", e
        )

    entries = data.get("entries", [])
    valid_entries = []
    recovered_count = 0

    for idx, entry_data in enumerate(entries):
        entry_valid, _ = _validate_entry(entry_data, idx)
        if not entry_valid:
            logger.warning("Skipping corrupted entry at index %d", idx)
        else:
            valid_entries.append(entry_data)
            recovered_count += 1

    total = len(entries)
    logger.info(
        "Recovery complete: %d/%d entries recovered", recovered_count, total
    )
    return valid_entries




# ---------------------------------------------------------------------------
# TF-IDF search utilities
# ---------------------------------------------------------------------------

# Tokenize text into lowercase words, individual CJK chars, and CJK bigrams
_WORD_RE = re.compile(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]')
_CJK_BIGRAM_RE = re.compile(r'[\u4e00-\u9fff]{2}')

# Common code terminology expansions (bidirectional)
_CODE_TERM_EXPANSIONS: dict[str, list[str]] = {
    "函数": ["function", "func", "method"],
    "function": ["函数", "func", "method"],
    "func": ["函数", "function", "method"],
    "method": ["函数", "function", "func"],
    "类": ["class", "type"],
    "class": ["类", "type"],
    "type": ["类", "class"],
    "变量": ["variable", "var"],
    "variable": ["变量", "var"],
    "var": ["变量", "variable"],
    "参数": ["parameter", "param", "argument", "arg"],
    "parameter": ["参数", "param", "argument"],
    "param": ["参数", "parameter", "arg"],
    "argument": ["参数", "parameter", "arg"],
    "属性": ["attribute", "attr", "property", "prop"],
    "attribute": ["属性", "attr", "property"],
    "property": ["属性", "attr", "prop"],
    "接口": ["interface"],
    "interface": ["接口"],
    "模块": ["module"],
    "module": ["模块"],
    "包": ["package"],
    "package": ["包"],
    "方法": ["method", "function"],
    "对象": ["object", "obj"],
    "object": ["对象", "obj"],
    "继承": ["inherit", "inheritance", "extends"],
    "inherit": ["继承"],
    "多态": ["polymorphism"],
    "封装": ["encapsulation", "encapsulate"],
    "异常": ["exception", "error"],
    "exception": ["异常"],
    "error": ["错误", "异常"],
    "错误": ["error", "bug"],
    "bug": ["错误", "bug", "缺陷"],
    "循环": ["loop", "iteration", "iterate"],
    "loop": ["循环"],
    "条件": ["condition"],
    "condition": ["条件"],
    "数组": ["array"],
    "array": ["数组"],
    "列表": ["list"],
    "list": ["列表"],
    "字典": ["dict", "dictionary", "map"],
    "dict": ["字典", "dictionary"],
    "dictionary": ["字典", "dict"],
    "map": ["字典", "映射"],
    "映射": ["map"],
    "集合": ["set"],
    "set": ["集合"],
    "字符串": ["string", "str"],
    "string": ["字符串"],
    "整数": ["int", "integer"],
    "integer": ["整数"],
    "浮点": ["float"],
    "float": ["浮点"],
    "布尔": ["bool", "boolean"],
    "boolean": ["布尔"],
    "同步": ["sync", "synchronous"],
    "异步": ["async", "asynchronous"],
    "async": ["异步"],
    "回调": ["callback"],
    "callback": ["回调"],
    "事件": ["event"],
    "event": ["事件"],
    "装饰器": ["decorator"],
    "decorator": ["装饰器"],
    "生成器": ["generator"],
    "generator": ["生成器"],
    "迭代器": ["iterator"],
    "iterator": ["迭代器"],
    "测试": ["test", "testing"],
    "test": ["测试"],
    "调试": ["debug", "debugging"],
    "debug": ["调试"],
    "配置": ["config", "configuration"],
    "config": ["配置"],
    "数据库": ["database", "db"],
    "database": ["数据库", "db"],
    "缓存": ["cache"],
    "cache": ["缓存"],
    "队列": ["queue"],
    "queue": ["队列"],
    "栈": ["stack"],
    "stack": ["栈"],
    "树": ["tree"],
    "tree": ["树"],
    "图": ["graph"],
    "graph": ["图"],
    "搜索": ["search"],
    "search": ["搜索"],
    "排序": ["sort", "sorting"],
    "sort": ["排序"],
    "文件": ["file"],
    "file": ["文件"],
    "路径": ["path"],
    "path": ["路径"],
    "网络": ["network"],
    "network": ["网络"],
    "请求": ["request"],
    "request": ["请求"],
    "响应": ["response"],
    "response": ["响应"],
}


def _expand_query_terms(terms: list[str], active_domains: list[str] | None = None) -> list[str]:
    """Expand query terms using code terminology + domain-specific dictionaries."""
    expanded = list(terms)
    for term in terms:
        if term in _CODE_TERM_EXPANSIONS:
            expanded.extend(_CODE_TERM_EXPANSIONS[term])
    # Domain-specific expansions
    if active_domains:
        for domain in active_domains:
            domain_dict = _DOMAIN_TERM_EXPANSIONS.get(domain, {})
            for term in terms:
                if term in domain_dict:
                    expanded.extend(domain_dict[term])
    return expanded


# ── Domain-specific term expansions ─────────────────────────────────

_DOMAIN_TERM_EXPANSIONS: dict[str, dict[str, list[str]]] = {
    "frontend": {
        "component": ["组件", "widget", "control", "element"],
        "组件": ["component", "widget", "control"],
        "form": ["表单", "input", "field"],
        "表单": ["form", "input", "field"],
        "style": ["样式", "css", "theme", "design"],
        "样式": ["style", "css", "theme"],
        "css": ["样式", "style", "theme", "tailwind"],
        "render": ["渲染", "display", "paint"],
        "渲染": ["render", "display"],
        "state": ["状态", "store", "context"],
        "状态": ["state", "store"],
        "hook": ["hooks", "钩子"],
        "router": ["路由", "navigation"],
        "路由": ["router", "navigation", "route"],
        "button": ["按钮", "btn"],
        "modal": ["弹窗", "dialog", "popup"],
        "layout": ["布局", "grid", "flex"],
        "布局": ["layout", "grid", "flexbox"],
        "animation": ["动画", "transition", "motion"],
        "event": ["事件", "handler", "listener"],
        "props": ["属性", "properties", "parameters"],
        "dom": ["文档", "document", "node", "element"],
        "responsive": ["响应式", "adaptive", "mobile"],
        "typescript": ["ts", "type"],
    },
    "backend": {
        "api": ["端点", "endpoint", "路由", "route", "handler"],
        "endpoint": ["端点", "api", "路由"],
        "route": ["路由", "path", "endpoint", "api"],
        "auth": ["认证", "鉴权", "login", "token", "jwt", "oauth"],
        "认证": ["auth", "authentication", "login"],
        "middleware": ["中间件", "interceptor", "filter"],
        "中间件": ["middleware", "interceptor"],
        "request": ["请求", "req"],
        "response": ["响应", "res", "reply"],
        "server": ["服务器", "服务端", "host"],
        "服务器": ["server", "host"],
        "queue": ["队列", "message", "mq", "worker"],
        "队列": ["queue", "message", "worker"],
        "cache": ["缓存", "redis", "memcache"],
        "缓存": ["cache", "redis"],
        "cron": ["定时", "schedule", "job", "task"],
        "定时": ["cron", "schedule", "timer"],
        "log": ["日志", "logging", "trace"],
        "日志": ["log", "logging"],
        "validate": ["校验", "验证", "sanitize", "check"],
        "校验": ["validate", "validation", "check"],
        "rate limit": ["限流", "throttle", "quota"],
        "限流": ["rate limit", "throttle"],
        "serialize": ["序列化", "marshal", "json"],
        "序列化": ["serialize", "marshal"],
    },
    "database": {
        "migration": ["迁移", "schema change", "ddl", "alembic", "flyway"],
        "迁移": ["migration", "schema change"],
        "schema": ["模式", "结构", "ddl", "table def"],
        "query": ["查询", "select", "sql"],
        "查询": ["query", "select", "read"],
        "index": ["索引", "btree", "hash"],
        "索引": ["index", "lookup"],
        "transaction": ["事务", "commit", "rollback", "acid"],
        "事务": ["transaction", "commit"],
        "connection": ["连接", "pool", "session"],
        "连接": ["connection", "pool"],
        "postgres": ["postgresql", "pg"],
        "orm": ["prisma", "typeorm", "sequelize", "drizzle", "sqlalchemy"],
        "backup": ["备份", "dump", "restore"],
        "备份": ["backup", "dump"],
        "replica": ["副本", "standby", "slave"],
        "partition": ["分区", "shard", "split"],
    },
    "devops": {
        "deploy": ["部署", "release", "ship"],
        "部署": ["deploy", "release"],
        "docker": ["容器", "container", "image"],
        "容器": ["docker", "container"],
        "ci": ["持续集成", "pipeline", "build"],
        "pipeline": ["流水线", "ci/cd", "workflow"],
        "monitor": ["监控", "alert", "observe", "metrics"],
        "监控": ["monitor", "alert", "metrics"],
        "secret": ["密钥", "credentials", "env"],
        "密钥": ["secret", "credentials", "token"],
        "kubernetes": ["k8s", "pod", "cluster"],
        "k8s": ["kubernetes", "cluster"],
        "nginx": ["反向代理", "proxy", "gateway"],
        "terraform": ["基础设施", "infrastructure", "iac"],
        "log": ["日志", "logging", "收集", "aggregate"],
        "backup": ["备份", "snapshot", "restore"],
    },
    "testing": {
        "test": ["测试", "spec", "assert"],
        "mock": ["模拟", "stub", "fake", "spy"],
        "模拟": ["mock", "stub", "fake"],
        "assert": ["断言", "expect", "should"],
        "断言": ["assert", "expect"],
        "coverage": ["覆盖率", "cover"],
        "e2e": ["端到端", "end-to-end", "integration"],
        "unit": ["单元", "unit test"],
        "fixture": ["夹具", "setup", "teardown"],
        "regression": ["回归", "replay"],
    },
}


@functools.lru_cache(maxsize=1024)
def _tokenize(text: str) -> list[str]:
    """Tokenize text into words for TF-IDF scoring.

    Handles alphanumeric words, individual CJK characters, and CJK bigrams
    for better Chinese text semantic matching.
    """
    tokens = [w.lower() for w in _WORD_RE.findall(text)]
    cjk_bigrams = [match.lower() for match in _CJK_BIGRAM_RE.findall(text)]
    return tokens + cjk_bigrams


# BM25 parameters
_BM25_K1 = 1.5  # Term frequency scaling
_BM25_B = 0.75  # Document length normalization


def _compute_tf(tokens: list[str]) -> dict[str, float]:
    """Compute term frequency for a list of tokens."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {term: count / total for term, count in counts.items()}


def _compute_idf(documents: list[list[str]]) -> dict[str, float]:
    """Compute inverse document frequency across documents.

    Uses smoothed IDF formula: log((N + 1) / (df + 1)) + 1
    """
    n = len(documents)
    if n == 0:
        return {}
    doc_freq: dict[str, int] = {}
    for doc_tokens in documents:
        seen = set(doc_tokens)
        for term in seen:
            doc_freq[term] = doc_freq.get(term, 0) + 1
    return {
        term: math.log((n + 1) / (df + 1)) + 1
        for term, df in doc_freq.items()
    }


def _compute_avgdl(documents: list[list[str]]) -> float:
    """Compute average document length."""
    if not documents:
        return 0.0
    return sum(len(doc) for doc in documents) / len(documents)


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float,
    *,
    k1: float = _BM25_K1,
    b: float = _BM25_B,
) -> float:
    """Compute Okapi BM25 score between query and document.

    Formula:
        score(q,d) = sum(IDF(qi) * (tf(qi,d) * (k1 + 1)) /
                         (tf(qi,d) + k1 * (1 - b + b * |d|/avgdl)))
    """
    if not query_tokens or not doc_tokens or avgdl == 0:
        return 0.0

    doc_len = len(doc_tokens)
    tf_doc = _compute_tf(doc_tokens)
    total_tokens = doc_len

    score = 0.0
    for term in set(query_tokens):
        if term not in idf:
            continue
        tf = tf_doc.get(term, 0.0)
        if tf == 0:
            continue
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (total_tokens / avgdl))
        score += idf[term] * (numerator / denominator)

    return score


def _tfidf_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    idf: dict[str, float],
    avgdl: float = 0.0,
) -> float:
    """Compute BM25 score between query and document.

    Note: This function name is kept for backward compatibility but now
    uses BM25 scoring internally for better short-text ranking.
    """
    return _bm25_score(query_tokens, doc_tokens, idf, avgdl)


def get_tfidf_keywords(text: str, top_n: int = 10) -> list[tuple[str, float]]:
    """Extract top N most important terms from text using TF scores.

    Useful for auto-categorization and understanding key topics in text.

    Args:
        text: Input text to analyze
        top_n: Number of top keywords to return

    Returns:
        List of (term, tf_score) tuples sorted by importance
    """
    tokens = _tokenize(text)
    if not tokens:
        return []
    tf = _compute_tf(tokens)
    sorted_terms = sorted(tf.items(), key=lambda x: x[1], reverse=True)
    return sorted_terms[:top_n]


# ---------------------------------------------------------------------------
# Auto-classification heuristics
# ---------------------------------------------------------------------------

_CLASSIFICATION_RULES: list[tuple[str, list[str], list[str]]] = [
    ("architecture", ["architecture", "design", "pattern", "api", "rest", "backend", "service", "架构", "设计", "模式"]),
    ("code-pattern", ["function", "method", "def", "class", "函数", "方法", "类"]),
    ("testing", ["test", "assert", "pytest", "unit", "测试", "断言"]),
    ("configuration", ["config", "settings", "env", "配置", "设置", "环境"]),
    ("workflow", ["git", "commit", "branch", "merge", "工作流", "分支", "合并"]),
    ("security", ["security", "auth", "permission", "安全", "认证", "权限"]),
    ("performance", ["performance", "optimization", "benchmark", "性能", "优化", "基准"]),
    ("convention", ["convention", "style", "naming", "规范", "风格", "命名"]),
]


def _auto_classify_content(content: str) -> tuple[str, list[str]]:
    """Analyze content and return (category, tags) using keyword heuristics.

    Supports both English and Chinese keywords. Returns "general" category
    with empty tags if no classification rules match.

    Args:
        content: Text content to classify

    Returns:
        Tuple of (category, tags) - e.g., ("architecture", ["design-pattern"])
    """
    content_lower = content.lower()
    category_scores: dict[str, int] = {}
    matched_tags: list[str] = []

    category_to_tags = {
        "architecture": ["design-pattern"],
        "code-pattern": ["function"],
        "testing": ["test"],
        "configuration": ["config"],
        "workflow": ["git"],
        "security": ["security"],
        "performance": ["optimization"],
        "convention": ["style"],
    }

    for category, keywords in (
        (rule[0], rule[1]) for rule in _CLASSIFICATION_RULES
    ):
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            category_scores[category] = score
            matched_tags.extend(category_to_tags.get(category, []))

    if not category_scores:
        return "general", []

    best_category = max(category_scores, key=category_scores.get)
    return best_category, matched_tags


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class MemoryScope(str, Enum):
    """Memory scope levels."""
    USER = "user"       # Cross-project, ~/.mini-code/memory/
    PROJECT = "project" # Project-shared, .mini-code-memory/
    LOCAL = "local"     # Project-local, .mini-code-memory-local/


class MemoryApprovalPolicy(str, Enum):
    """Typed authority policy for durable Memory creation."""

    USER_EXPLICIT = "user_explicit"
    USER_REVIEW_REQUIRED = "user_review_required"


class MemoryTier(str, Enum):
    """Memory tier for multi-level storage architecture.

    Inspired by human memory models (Atkinson-Shiffrin) and Letta/MemGPT:
      WORKING    → current session, full detail, fast access
      SHORT_TERM → recent (< 7 days), full detail
      LONG_TERM  → consolidated (< 30 days), compressed
      ARCHIVAL   → permanent, heavily summarized
    """
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    ARCHIVAL = "archival"


_VALID_SCOPES = {m.value for m in MemoryScope}
_ACTIVE_LIFECYCLE = "active"
_SAFETY_SAFE = "safe"
_SAFETY_SUSPICIOUS = "suspicious"
_SAFETY_UNSAFE = "unsafe"
_VALID_SAFETY_STATUSES = {_SAFETY_SAFE, _SAFETY_SUSPICIOUS, _SAFETY_UNSAFE}

_APPROVAL_APPROVED = "approved"
_APPROVAL_PENDING = "pending"
_APPROVAL_REJECTED = "rejected"
_VALID_APPROVAL_STATUSES = {
    _APPROVAL_APPROVED,
    _APPROVAL_PENDING,
    _APPROVAL_REJECTED,
}

# Backward-compatible names for older callers that import these constants.
_SAFETY_ACTIVE = _SAFETY_SAFE
_SAFETY_REJECTED = _SAFETY_UNSAFE
_NON_INJECTABLE_LIFECYCLES = {
    "pending",
    "rejected",
    "deprecated",
    "invalid",
    "archived_duplicate",
}


@dataclass(frozen=True)
class MemorySafetyResult:
    """Result of scanning a proposed durable memory write."""

    status: str
    reason: str = ""
    risk: str = "low"

    @property
    def allowed(self) -> bool:
        return self.status == _SAFETY_SAFE

    @property
    def needs_approval(self) -> bool:
        return self.status == _SAFETY_SUSPICIOUS

    @property
    def rejected(self) -> bool:
        return self.status == _SAFETY_UNSAFE


@dataclass(frozen=True, slots=True)
class MemoryApprovalMutation:
    """Typed result of one durable pending-Memory decision."""

    memory_id: str
    scope: MemoryScope
    status: str
    decision: str
    decision_accepted: bool
    updated_at: float
    compatibility_message: str = ""


def _normalize_memory_content(content: str) -> str:
    """Normalize memory content for duplicate detection."""
    return re.sub(r"\s+", " ", content.strip().lower())


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_HOMOGLYPH_TRANSLATION = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "а": "a", "е": "e", "і": "i", "ј": "j", "о": "o", "р": "p",
    "с": "c", "ѕ": "s", "у": "y", "х": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
    "Υ": "Y", "Ζ": "Z",
    "α": "a", "β": "b", "ε": "e", "η": "h", "ι": "i", "κ": "k",
    "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "χ": "x", "υ": "y",
})


def _normalize_safety_text(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).translate(_HOMOGLYPH_TRANSLATION)
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    return _normalize_memory_content(normalized)


def _collapse_spaced_ascii_letters(text: str) -> str:
    """Collapse simple letter-spaced obfuscations like 'i g n o r e'."""
    return re.sub(
        r"(?i)(?<![a-z])(?:[a-z]\s+){2,}[a-z](?![a-z])",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        text,
    )


def _decoded_base64_fragments(text: str) -> list[str]:
    decoded: list[str] = []
    for match in re.findall(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/]{24,}={0,2})(?![A-Za-z0-9+/=])", text):
        if len(match) % 4 != 0:
            continue
        try:
            raw = base64.b64decode(match, validate=True)
            if not raw or b"\x00" in raw[:64]:
                continue
            decoded_text = raw[:2000].decode("utf-8", errors="ignore")
        except (binascii.Error, ValueError):
            continue
        if decoded_text.strip():
            decoded.append(decoded_text)
    return decoded


def _memory_safety_variants(content: str) -> list[str]:
    base = _normalize_safety_text(content)
    variants = [base, _collapse_spaced_ascii_letters(base)]
    for decoded in _decoded_base64_fragments(content):
        normalized = _normalize_safety_text(decoded)
        variants.append(normalized)
        variants.append(_collapse_spaced_ascii_letters(normalized))
    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        if variant and variant not in seen:
            seen.add(variant)
            deduped.append(variant)
    return deduped


def _memory_safety_variant_records(content: str) -> list[tuple[str, bool]]:
    """Return normalized variants plus whether each came from encoded content."""
    records: list[tuple[str, bool]] = []
    seen: set[str] = set()

    base = _normalize_safety_text(content)
    for variant in (base, _collapse_spaced_ascii_letters(base)):
        if variant and variant not in seen:
            seen.add(variant)
            records.append((variant, False))

    for decoded in _decoded_base64_fragments(content):
        normalized = _normalize_safety_text(decoded)
        for variant in (normalized, _collapse_spaced_ascii_letters(normalized)):
            if variant and variant not in seen:
                seen.add(variant)
                records.append((variant, True))
    return records


_UNTRUSTED_FRAMING_RE = re.compile(
    r"\b("
    r"quote|quoted|example|sample|fixture|test case|unit test|regression test|"
    r"incident|log|document|documentation|research note|security research|"
    r"prompt injection|malicious input|attack sample|transcript"
    r")\b|"
    r"(引用|示例|样本|测试|日志|文档|研究|安全研究|攻击样本|恶意输入|转录)",
    flags=re.I,
)


def _has_untrusted_framing(content: str, normalized_variant: str) -> bool:
    """Detect text that discusses or quotes risky instructions instead of issuing them."""
    raw_normalized = _normalize_safety_text(content)
    if _UNTRUSTED_FRAMING_RE.search(content) or _UNTRUSTED_FRAMING_RE.search(raw_normalized):
        return True
    # Markdown quote or fenced block around the matched instruction is not proof
    # of safety, but it is enough to require human review rather than reject.
    if re.search(r"(^|\n)\s*(>|```)", content):
        return True
    return _UNTRUSTED_FRAMING_RE.search(normalized_variant) is not None


def _classified_safety_result(
    content: str,
    variant: str,
    reason: str,
    *,
    encoded: bool = False,
) -> MemorySafetyResult:
    if encoded:
        return MemorySafetyResult(
            _SAFETY_SUSPICIOUS,
            f"encoded content may contain unsafe instructions: {reason}",
            "medium",
        )
    if _has_untrusted_framing(content, variant):
        return MemorySafetyResult(
            _SAFETY_SUSPICIOUS,
            f"quoted or documented unsafe instruction requires approval: {reason}",
            "medium",
        )
    return MemorySafetyResult(_SAFETY_UNSAFE, reason, "high")


def _coerce_scope(value: Any, default: MemoryScope = MemoryScope.USER) -> MemoryScope:
    if isinstance(value, MemoryScope):
        return value
    try:
        return MemoryScope(str(value))
    except (TypeError, ValueError):
        return default


def _coerce_tier(value: Any, default: MemoryTier = MemoryTier.SHORT_TERM) -> MemoryTier:
    if isinstance(value, MemoryTier):
        return value
    try:
        return MemoryTier(str(value))
    except (TypeError, ValueError):
        return default


def _safe_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, str)]


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_safety_status(value: Any) -> str:
    status = str(value or _SAFETY_SAFE).lower()
    if status in {"active", "allowed", "approved", _SAFETY_SAFE}:
        return _SAFETY_SAFE
    if status in {"pending", "review", _SAFETY_SUSPICIOUS}:
        return _SAFETY_SUSPICIOUS
    if status in {"rejected", "blocked", "invalid", _SAFETY_UNSAFE}:
        return _SAFETY_UNSAFE
    return _SAFETY_SUSPICIOUS


def _normalize_approval_status(value: Any) -> str:
    status = str(value or _APPROVAL_PENDING).lower()
    if status in {"active", "approved", "allow", "allowed"}:
        return _APPROVAL_APPROVED
    if status in {"pending", "review", "needs_review"}:
        return _APPROVAL_PENDING
    if status in {"rejected", "blocked", "invalid", "unsafe"}:
        return _APPROVAL_REJECTED
    return _APPROVAL_PENDING


def _json_default(value: Any) -> str:
    return repr(value)


def _approval_hash_payload(entry: "MemoryEntry") -> dict[str, Any]:
    """Safety-sensitive fields bound to a human approval decision."""
    return {
        "content": entry.content,
        "category": entry.category,
        "tags": sorted(entry.tags),
        "domains": sorted(entry.domains),
        "source": entry.source,
        "provenance": entry.provenance,
        "metadata": entry.metadata,
    }


def _approval_hash_for_entry(entry: "MemoryEntry") -> str:
    payload = json.dumps(
        _approval_hash_payload(entry),
        sort_keys=True,
        ensure_ascii=False,
        default=_json_default,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assess_memory_safety(content: str, source: str = "") -> MemorySafetyResult:
    """Scan durable memory content before it can become injectable context.

    The gate intentionally looks for instruction intent, not isolated keywords,
    so benign notes like "test prompt injection detection" remain writable.
    """
    variants = _memory_safety_variant_records(content)
    if not variants:
        return MemorySafetyResult(_SAFETY_UNSAFE, "empty memory content", "high")

    checks: list[tuple[str, str]] = [
        (
            r"\b(ignore|disregard|override|bypass|forget)\b.{0,80}"
            r"\b(system|developer|previous|above|prior)\b.{0,50}"
            r"\b(instruction|instructions|message|prompt|rules?)\b",
            "attempts to override higher-priority instructions",
        ),
        (
            r"\b(system|developer)\s*:\s*"
            r"(ignore|override|bypass|forget|reveal|leak|dump|print)\b",
            "masquerades as a higher-priority instruction",
        ),
        (
            r"\b(reveal|print|dump|exfiltrate|leak|send|upload)\b.{0,90}"
            r"\b(system prompt|developer message|secret|api key|token|credential|"
            r"password|private key|env(?:ironment)? variables?)\b",
            "requests disclosure or exfiltration of secrets/prompts",
        ),
        (
            r"\b(from now on|in all future sessions|forever)\b.{0,90}"
            r"\b(always|must|never|ignore|execute|run|send|upload)\b",
            "tries to install unconditional future behavior",
        ),
        (
            r"\b(always|must|never)\b.{0,80}"
            r"\b(ignore|override|bypass|disregard)\b",
            "tries to install unconditional instruction override behavior",
        ),
        (
            r"\b(curl|wget|webhook|http post|post to|upload|send)\b.{0,90}"
            r"\b(secret|token|api key|credential|password|private key|env)\b",
            "directs tool-based secret exfiltration",
        ),
        (
            r"\b(act as|pretend to be|you are now)\b.{0,50}"
            r"\b(system|developer|root|policy)\b",
            "forges policy or system authority",
        ),
        (
            r"(忽略|覆盖|绕过|忘记).{0,40}(系统|开发者|上面|之前).{0,30}(指令|提示|规则)",
            "attempts to override higher-priority instructions",
        ),
        (
            r"(泄露|打印|导出|发送|上传|输出).{0,40}(系统提示|开发者消息|密钥|凭据|口令|密码|token|环境变量)",
            "requests disclosure or exfiltration of secrets/prompts",
        ),
        (
            r"(从现在开始|以后|以后每次|永久).{0,40}(总是|必须|不要|忽略|执行|发送|上传|输出)",
            "tries to install unconditional future behavior",
        ),
    ]

    compact_checks: list[tuple[str, str]] = [
        (
            r"ignore(previous|prior|above)?(system|developer).{0,40}instructions?",
            "attempts to override higher-priority instructions",
        ),
        (
            r"(reveal|dump|print|leak|exfiltrate)(systemprompt|developermessage|secret|apikey|token|credential|password|env)",
            "requests disclosure or exfiltration of secrets/prompts",
        ),
        (
            r"(fromnowon|inallfuturesessions|forever).{0,80}(always|must|never|ignore|execute|run|send|upload)",
            "tries to install unconditional future behavior",
        ),
    ]

    for text, encoded in variants:
        for pattern, reason in checks:
            if re.search(pattern, text, flags=re.I):
                return _classified_safety_result(
                    content,
                    text,
                    reason,
                    encoded=encoded,
                )
        compact = re.sub(r"[\s`*_~.\-:/\\]+", "", text)
        for pattern, reason in compact_checks:
            if re.search(pattern, compact, flags=re.I):
                return _classified_safety_result(
                    content,
                    text,
                    reason,
                    encoded=encoded,
                )

    return MemorySafetyResult(_SAFETY_SAFE, "", "low")


@dataclass
class MemoryEntry:
    """A single memory entry (fact, pattern, decision, etc.)."""
    id: str
    scope: MemoryScope
    category: str  # e.g., "architecture", "convention", "decision", "pattern"
    content: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    usage_count: int = 0  # How often this was referenced
    domains: list[str] = field(default_factory=list)  # Domain classification
    # Multi-tier memory architecture
    tier: MemoryTier = MemoryTier.SHORT_TERM
    last_accessed: float = field(default_factory=time.time)
    related_to: list[str] = field(default_factory=list)  # Related memory IDs
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    retrieval_count: int = 0
    injection_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used: float = 0.0
    usefulness_score: float = 0.0
    corroborated_success_count: int = 0
    corroborated_failure_count: int = 0
    corroborated_usefulness_score: float = 0.0
    lifecycle_status: str = _ACTIVE_LIFECYCLE
    tier_reason: str = ""
    deprecated_at: float | None = None
    curator_locked: bool = False
    safety_status: str = _SAFETY_SAFE
    safety_reason: str = ""
    approval_status: str = _APPROVAL_APPROVED
    approval_content_hash: str = ""
    approval_reason: str = ""
    approval_actor: str = "safety_gate"
    approval_decided_at: float = 0.0
    approval_policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT
    _cached_tokens: list[str] | None = field(default=None, repr=False)
    _last_relevance: float = field(default=0.0, repr=False, compare=False)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MemoryEntry):
            return NotImplemented
        return self.id == other.id

    def get_tokens(self) -> list[str]:
        if self._cached_tokens is None:
            text = (
                f"{self.content} {self.category} "
                f"{' '.join(self.tags)} {' '.join(self.domains)}"
            )
            self._cached_tokens = _tokenize(text)
        return self._cached_tokens

    def invalidate_tokens(self) -> None:
        self._cached_tokens = None

    @property
    def is_active(self) -> bool:
        return (
            self.approval_status == _APPROVAL_APPROVED
            and self.safety_status != _SAFETY_UNSAFE
            and self.lifecycle_status == _ACTIVE_LIFECYCLE
            and not self.curator_locked
            and self.tier != MemoryTier.ARCHIVAL
        )
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "scope": self.scope.value,
            "category": self.category,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "usage_count": self.usage_count,
            "domains": self.domains,
            "tier": self.tier.value,
            "last_accessed": self.last_accessed,
            "related_to": self.related_to,
            "metadata": self.metadata,
            "source": self.source,
            "provenance": self.provenance,
            "retrieval_count": self.retrieval_count,
            "injection_count": self.injection_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_used": self.last_used,
            "usefulness_score": self.usefulness_score,
            "corroborated_success_count": self.corroborated_success_count,
            "corroborated_failure_count": self.corroborated_failure_count,
            "corroborated_usefulness_score": self.corroborated_usefulness_score,
            "lifecycle_status": self.lifecycle_status,
            "tier_reason": self.tier_reason,
            "deprecated_at": self.deprecated_at,
            "curator_locked": self.curator_locked,
            "safety_status": self.safety_status,
            "safety_reason": self.safety_reason,
            "approval_status": self.approval_status,
            "approval_content_hash": self.approval_content_hash,
            "approval_reason": self.approval_reason,
            "approval_actor": self.approval_actor,
            "approval_decided_at": self.approval_decided_at,
            "approval_policy": self.approval_policy.value,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            scope=_coerce_scope(data.get("scope", "user")),
            category=data.get("category", "general"),
            content=data["content"],
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            tags=_safe_str_list(data.get("tags", [])),
            usage_count=int(data.get("usage_count", 0) or 0),
            domains=_safe_str_list(data.get("domains", [])),
            tier=_coerce_tier(data.get("tier", "short_term")),
            last_accessed=data.get("last_accessed", time.time()),
            related_to=_safe_str_list(data.get("related_to", [])),
            metadata=_safe_dict(data.get("metadata", {})),
            source=str(data.get("source", "") or ""),
            provenance=_safe_dict(data.get("provenance", {})),
            retrieval_count=int(data.get("retrieval_count", 0) or 0),
            injection_count=int(data.get("injection_count", 0) or 0),
            success_count=int(data.get("success_count", 0) or 0),
            failure_count=int(data.get("failure_count", 0) or 0),
            last_used=float(data.get("last_used", 0.0) or 0.0),
            usefulness_score=float(data.get("usefulness_score", 0.0) or 0.0),
            corroborated_success_count=int(data.get("corroborated_success_count", 0) or 0),
            corroborated_failure_count=int(data.get("corroborated_failure_count", 0) or 0),
            corroborated_usefulness_score=float(
                data.get("corroborated_usefulness_score", 0.0) or 0.0
            ),
            lifecycle_status=str(data.get("lifecycle_status", _ACTIVE_LIFECYCLE) or _ACTIVE_LIFECYCLE),
            tier_reason=str(data.get("tier_reason", "") or ""),
            deprecated_at=(
                float(data["deprecated_at"])
                if isinstance(data.get("deprecated_at"), (int, float))
                else None
            ),
            curator_locked=bool(data.get("curator_locked", False)),
            safety_status=_normalize_safety_status(data.get("safety_status", _SAFETY_SAFE)),
            safety_reason=str(data.get("safety_reason", "") or ""),
            approval_status=_normalize_approval_status(data.get("approval_status", "")),
            approval_content_hash=str(data.get("approval_content_hash", "") or ""),
            approval_reason=str(data.get("approval_reason", "") or ""),
            approval_actor=str(data.get("approval_actor", "") or ""),
            approval_decided_at=float(data.get("approval_decided_at", 0.0) or 0.0),
            approval_policy=(
                MemoryApprovalPolicy(data.get("approval_policy", MemoryApprovalPolicy.USER_EXPLICIT.value))
                if data.get("approval_policy", MemoryApprovalPolicy.USER_EXPLICIT.value)
                in {policy.value for policy in MemoryApprovalPolicy}
                else MemoryApprovalPolicy.USER_EXPLICIT
            ),
        )


@dataclass
class MemoryFile:
    """Represents a MEMORY.md file content with indexed lookups."""
    scope: MemoryScope
    entries: list[MemoryEntry] = field(default_factory=list)
    max_entries: int = 200  # Claude Code limit
    max_size_bytes: int = 25 * 1024  # 25KB limit
    _id_index: dict[str, MemoryEntry] = field(default_factory=dict, repr=False)
    _tag_index: dict[str, set[MemoryEntry]] = field(default_factory=dict, repr=False)
    _category_index: dict[str, list[MemoryEntry]] = field(default_factory=dict, repr=False)
    _tokens_cache: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _idf_cache: dict[str, float] | None = field(default=None, repr=False)
    _avgdl_cache: float | None = field(default=None, repr=False)
    _cache_dirty: bool = field(default=True, repr=False)

    def _rebuild_indices(self) -> None:
        self._id_index.clear()
        self._tag_index.clear()
        self._category_index.clear()
        self._tokens_cache.clear()
        self._idf_cache = None
        self._avgdl_cache = None
        for entry in self.entries:
            self._id_index[entry.id] = entry
            for tag in entry.tags:
                if tag not in self._tag_index:
                    self._tag_index[tag] = set()
                self._tag_index[tag].add(entry)
            cat = entry.category
            if cat not in self._category_index:
                self._category_index[cat] = []
            self._category_index[cat].append(entry)
            self._tokens_cache[entry.id] = entry.get_tokens()
        # Precompute IDF and avgdl
        if self._tokens_cache:
            all_tokens = list(self._tokens_cache.values())
            self._idf_cache = _compute_idf(all_tokens)
            self._avgdl_cache = _compute_avgdl(all_tokens)
        self._cache_dirty = False

    def _ensure_cache_valid(self) -> None:
        if self._cache_dirty:
            self._rebuild_indices()

    def _invalidate_cache(self) -> None:
        self._cache_dirty = True
        self._idf_cache = None
        self._avgdl_cache = None

    @property
    def size_bytes(self) -> int:
        """Estimate size in bytes."""
        return sum(len(e.content) for e in self.entries)
    
    def add_entry(self, entry: MemoryEntry) -> None:
        """Unsafe/internal add: caller must have applied safety and approval."""
        self.entries.append(entry)
        self._invalidate_cache()
        self._enforce_limits()
        self._ensure_cache_valid()
    
    def update_entry(self, entry_id: str, content: str) -> bool:
        """Update existing entry using index."""
        self._ensure_cache_valid()
        entry = self._id_index.get(entry_id)
        if entry is None:
            return False
        entry.content = content
        entry.updated_at = time.time()
        entry.invalidate_tokens()
        self._invalidate_cache()
        return True
    
    def delete_entry(self, entry_id: str) -> bool:
        """Delete entry and rebuild all indexes defensively."""
        self._ensure_cache_valid()
        entry = self._id_index.get(entry_id)
        if entry is None:
            entry = next((e for e in self.entries if e.id == entry_id), None)
        if entry is None:
            return False
        original_len = len(self.entries)
        self.entries = [e for e in self.entries if e.id != entry_id]
        if len(self.entries) == original_len:
            return False
        self._rebuild_indices()
        return True
    
    def get_entries_by_category(self, category: str) -> list[MemoryEntry]:
        """Get entries filtered by category using index."""
        self._ensure_cache_valid()
        return list(self._category_index.get(category, []))
    
    def search(
        self,
        query: str,
        active_domains: list[str] | None = None,
        *,
        record_usage: bool = True,
    ) -> list[MemoryEntry]:
        """Search entries by keyword with BM25 + domain relevance scoring.

        Combines BM25 semantic relevance with usage frequency and optional
        domain-based boosting (soft blend, not hard filtering).
        Domain score uses Jaccard similarity between entry domains and active domains.
        """
        candidate_entries = [entry for entry in self.entries if entry.is_active]
        if not candidate_entries:
            return []

        query_tokens = _tokenize(query)
        query_tokens = _expand_query_terms(query_tokens, active_domains=active_domains)
        if not query_tokens:
            return []

        query_lower = query.lower()
        query_terms = query_lower.split()

        entry_tokens = []
        for entry in candidate_entries:
            text = (
                f"{entry.content} {entry.category} "
                f"{' '.join(entry.tags)} {' '.join(entry.domains)}"
            )
            entry_tokens.append(_tokenize(text))

        idf = _compute_idf(entry_tokens)
        avgdl = _compute_avgdl(entry_tokens)

        scored: list[tuple[float, MemoryEntry]] = []
        for i, entry in enumerate(candidate_entries):
            bm25 = _bm25_score(query_tokens, entry_tokens[i], idf, avgdl)

            substring_score = 0.0
            content_lower = entry.content.lower()
            if query_lower in content_lower:
                substring_score = 2.0
            elif any(q in content_lower for q in query_terms):
                substring_score = 1.0

            tag_score = 0.0
            exact_tag_match = any(
                tag.lower() == query_lower for tag in entry.tags
            )
            partial_tag_match = any(
                query_lower in tag.lower() for tag in entry.tags
            )
            if exact_tag_match:
                tag_score = 5.0
            elif partial_tag_match:
                tag_score = 1.5
            if query_lower in entry.category.lower():
                tag_score += 1.0

            match_score = bm25 + substring_score + tag_score
            if match_score <= 0:
                continue

            # Domain score: Jaccard similarity between entry.domains and active_domains
            domain_score = 0.0
            if active_domains and entry.domains:
                entry_set = set(entry.domains)
                active_set = set(active_domains)
                intersection = entry_set & active_set
                union = entry_set | active_set
                domain_score = len(intersection) / len(union) if union else 0.0

            # Soft blend: BM25 dominates, domain provides light steering
            final_relevance = match_score * 0.7 + domain_score * 0.3

            usage_bonus = math.log1p(entry.usage_count) * 0.2
            feedback_bonus = entry.usefulness_score * 0.2
            age_hours = (time.time() - entry.updated_at) / 3600
            recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5

            total_score = final_relevance + usage_bonus + feedback_bonus + recency_bonus
            entry._last_relevance = total_score
            scored.append((total_score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        if record_usage:
            for _, entry in scored[:10]:
                entry.retrieval_count += 1
        return [entry for _, entry in scored]
    
    def _enforce_limits(self) -> None:
        """Remove oldest entries if exceeding limits."""
        removed = False
        # Check entry count
        while len(self.entries) > self.max_entries:
            self.entries.pop(0)  # Remove oldest
            removed = True
        
        # Check size
        while self.size_bytes > self.max_size_bytes and self.entries:
            self.entries.pop(0)
            removed = True

        if removed:
            self._rebuild_indices()
    
    def format_as_markdown(self, include_header: bool = True) -> str:
        """Format as MEMORY.md content."""
        lines = []
        
        if include_header:
            scope_names = {
                MemoryScope.USER: "User Memory",
                MemoryScope.PROJECT: "Project Memory",
                MemoryScope.LOCAL: "Local Memory",
            }
            lines.append(f"# {scope_names[self.scope]}")
            lines.append("")
            lines.append(f"*Last updated: {time.strftime('%Y-%m-%d %H:%M')}*")
            lines.append("")
        
        # Group by category
        categories: dict[str, list[MemoryEntry]] = {}
        for entry in self.entries:
            if entry.category not in categories:
                categories[entry.category] = []
            categories[entry.category].append(entry)
        
        for category, entries in categories.items():
            lines.append(f"## {category.title()}")
            lines.append("")
            for entry in entries:
                tags_str = f" `{' '.join(entry.tags)}`" if entry.tags else ""
                lines.append(f"- {entry.content}{tags_str}")
            lines.append("")
        
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Memory Manager
# ---------------------------------------------------------------------------

@dataclass
class MemoryPaths:
    """Paths for memory files at different scopes."""
    user_memory: Path
    project_memory: Path
    local_memory: Path
    
    @classmethod
    def for_workspace(cls, workspace: str) -> "MemoryPaths":
        """Create memory paths for a workspace."""
        workspace_path = Path(workspace)
        
        return cls(
            user_memory=MINI_CODE_DIR / "memory",
            project_memory=workspace_path / ".mini-code-memory",
            local_memory=workspace_path / ".mini-code-memory-local",
        )


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|credential|authorization|bearer)\b"
    r"(\s*[:=]\s*)[^\s,;]+"
)
_BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{3,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{20,255})\b"
)
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|credential|authorization|bearer|private[_-]?key)"
)


def _sanitize_untrusted_text(value: Any, max_chars: int = 600) -> str:
    text = str(value)
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = _CONTROL_RE.sub(lambda m: f"\\x{ord(m.group(0)):02x}", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _BEARER_VALUE_RE.sub("Bearer [REDACTED]", text)
    text = _TOKEN_VALUE_RE.sub("[REDACTED]", text)
    text = _CREDENTIAL_URL_RE.sub("[REDACTED_URL]", text)
    if len(text) > max_chars:
        return text[:max_chars] + f"... [truncated {len(text) - max_chars} chars]"
    return text


def _redact_audit_value(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                result[key_str] = "[REDACTED]"
            else:
                result[key_str] = _redact_audit_value(nested, depth + 1)
        return result
    if isinstance(value, list):
        return [_redact_audit_value(v, depth + 1) for v in value[:20]]
    if isinstance(value, str):
        return _sanitize_untrusted_text(value, max_chars=240)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _sanitize_untrusted_text(repr(value), max_chars=160)


def _coordinated_scope_write(method):
    """Run a scope-first manager mutation against freshly loaded authority."""

    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        if self.in_write_transaction:
            return method(self, *args, **kwargs)
        raw_scope = kwargs.get("scope", args[0] if args else MemoryScope.PROJECT)
        scope = _coerce_scope(raw_scope, MemoryScope.PROJECT)
        return self.coordinated_write(
            (scope,), lambda: method(self, *args, **kwargs)
        )

    return wrapped


def _coordinated_all_write(method):
    """Run a potentially multi-scope mutation against freshly loaded authority."""

    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        if self.in_write_transaction:
            return method(self, *args, **kwargs)
        return self.coordinated_write(
            tuple(MemoryScope), lambda: method(self, *args, **kwargs)
        )

    return wrapped


class MemoryManager:
    """Manages layered memory system."""
    
    def __init__(
        self,
        workspace: str | Path | None = None,
        *,
        project_root: str | Path | None = None,
        store_timeout: float = 5.0,
        readonly_load: bool = False,
    ):
        # Backward compatibility: older call sites pass `project_root=...`.
        resolved_workspace = workspace if workspace is not None else project_root
        if resolved_workspace is None:
            resolved_workspace = Path.cwd()

        self.workspace = str(resolved_workspace)
        if not isinstance(readonly_load, bool):
            raise TypeError("readonly_load must be a bool")
        self._recover_on_load = not readonly_load
        self.paths = MemoryPaths.for_workspace(self.workspace)
        self._store = MemoryStoreCoordinator(MINI_CODE_DIR, timeout=store_timeout)
        self._disk_revisions: dict[MemoryScope, str] = {}
        self.memories: dict[MemoryScope, MemoryFile] = {
            MemoryScope.USER: MemoryFile(scope=MemoryScope.USER),
            MemoryScope.PROJECT: MemoryFile(scope=MemoryScope.PROJECT),
            MemoryScope.LOCAL: MemoryFile(scope=MemoryScope.LOCAL),
        }
        self.approval_audit: dict[MemoryScope, list[dict[str, Any]]] = {
            MemoryScope.USER: [],
            MemoryScope.PROJECT: [],
            MemoryScope.LOCAL: [],
        }
        self._last_retrieval_result: Any = None
        with self._store.transaction():
            self._load_all()
            self._disk_revisions = {
                scope: self._scope_disk_revision(scope) for scope in MemoryScope
            }

    @property
    def in_write_transaction(self) -> bool:
        return self._store.in_transaction

    def _scope_disk_revision(self, scope: MemoryScope) -> str:
        digest = hashlib.sha256()
        for filename in ("memory.json", "approval_audit.json"):
            path = self._get_scope_path(scope) / filename
            digest.update(filename.encode("ascii"))
            try:
                digest.update(path.read_bytes())
            except FileNotFoundError:
                digest.update(b"\0missing")
            except OSError as error:
                raise MemoryStoreConflict("Memory authority could not be read") from error
        return digest.hexdigest()

    def _reload_scopes(self, scopes: tuple[MemoryScope, ...]) -> None:
        for scope in scopes:
            self.memories[scope] = MemoryFile(scope=scope)
            self.approval_audit[scope] = []
            self._load_approval_audit(scope)
            self._load_scope(scope)
            if self._recover_on_load:
                self._auto_recover_scope(scope)
            self._disk_revisions[scope] = self._scope_disk_revision(scope)

    def coordinated_write(
        self,
        scopes: tuple[MemoryScope, ...] | list[MemoryScope],
        operation,
        *,
        reject_stale: bool = False,
    ):
        """Reload, validate and mutate one or more scopes under the store lock."""
        normalized = tuple(dict.fromkeys(_coerce_scope(scope) for scope in scopes))
        if self.in_write_transaction:
            return operation()
        with self._store.transaction():
            stale = any(
                scope in self._disk_revisions
                and self._disk_revisions[scope] != self._scope_disk_revision(scope)
                for scope in normalized
            )
            if stale:
                self._reload_scopes(normalized)
                if reject_stale:
                    raise MemoryStoreConflict("Memory content changed")
            try:
                result = operation()
            except BaseException:
                # A failed audit/state write must not leave this live manager
                # exposing an uncommitted in-memory approval as injectable.
                try:
                    self._reload_scopes(normalized)
                except BaseException:  # preserve the original write failure
                    pass
                raise
            for scope in normalized:
                self._disk_revisions[scope] = self._scope_disk_revision(scope)
            return result
    
    def _load_all(self) -> None:
        """Load all memory files."""
        for scope in MemoryScope:
            self._load_approval_audit(scope)
            self._load_scope(scope)
            if self._recover_on_load:
                self._auto_recover_scope(scope)
    
    def _auto_recover_scope(self, scope: MemoryScope) -> None:
        """Check integrity and auto-recover if issues are found.

        After loading, validates the memory state. If integrity issues
        are detected, attempts to recover by removing invalid entries
        and deduplicating IDs.

        Args:
            scope: Memory scope to check and recover
        """
        result = self.check_integrity(scope)
        if not result["is_valid"]:
            logger.warning(
                "Integrity check failed for scope %s: %d issues found. "
                "Attempting auto-recovery...",
                scope.value,
                len(result["issues"]),
            )
            self._recover_scope(scope)
    
    def _recover_scope(self, scope: MemoryScope) -> None:
        """Attempt to recover a scope with integrity issues.

        Removes entries with invalid IDs, deduplicates IDs (keeps first),
        and fixes entries with empty content or category.

        Args:
            scope: Memory scope to recover
        """
        entries = self.memories[scope].entries
        seen_ids: set[str] = set()
        recovered: list[MemoryEntry] = []
        removed_count = 0
        fixed_count = 0

        for entry in entries:
            if not entry.id or not isinstance(entry.id, str):
                logger.warning(
                    "Removing entry with invalid ID during recovery"
                )
                removed_count += 1
                continue

            if entry.id in seen_ids:
                logger.warning(
                    "Removing duplicate entry with ID '%s'", entry.id
                )
                removed_count += 1
                continue

            if not entry.category or not isinstance(entry.category, str):
                entry.category = "general"
                fixed_count += 1

            if not entry.content or not isinstance(entry.content, str):
                logger.warning(
                    "Removing entry '%s' with empty content", entry.id
                )
                removed_count += 1
                continue

            seen_ids.add(entry.id)
            recovered.append(entry)

        self.memories[scope].entries = recovered
        self.memories[scope]._rebuild_indices()
        self._save_scope(scope)

        logger.info(
            "Recovery complete for scope %s: %d entries recovered, "
            "%d removed, %d fixed",
            scope.value,
            len(recovered),
            removed_count,
            fixed_count,
        )

    @staticmethod
    def _risk_for_safety(status: str) -> str:
        if status == _SAFETY_UNSAFE:
            return "high"
        if status == _SAFETY_SUSPICIOUS:
            return "medium"
        return "low"

    @staticmethod
    def _approval_for_safety(
        status: str,
        policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT,
    ) -> str:
        if status == _SAFETY_UNSAFE:
            return _APPROVAL_REJECTED
        if (
            status == _SAFETY_SUSPICIOUS
            or policy == MemoryApprovalPolicy.USER_REVIEW_REQUIRED
        ):
            return _APPROVAL_PENDING
        return _APPROVAL_APPROVED

    def _set_entry_safety_and_approval(
        self,
        entry: MemoryEntry,
        safety: MemorySafetyResult,
        *,
        actor: str,
        reason: str = "",
        lifecycle_status: str | None = None,
        approval_policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT,
    ) -> None:
        """Apply the safety -> approval state transition to an entry."""
        entry.safety_status = _normalize_safety_status(safety.status)
        entry.safety_reason = safety.reason
        entry.approval_status = self._approval_for_safety(
            entry.safety_status, approval_policy
        )
        entry.approval_policy = approval_policy
        entry.approval_reason = reason or safety.reason
        entry.approval_actor = actor
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = _approval_hash_for_entry(entry)
        if entry.safety_status == _SAFETY_UNSAFE:
            entry.lifecycle_status = "rejected"
            entry.tier_reason = "safety_gate"
            entry.curator_locked = False
        elif lifecycle_status is not None:
            entry.lifecycle_status = lifecycle_status

    def _entry_hash_matches_approval(self, entry: MemoryEntry) -> bool:
        return bool(entry.approval_content_hash) and (
            entry.approval_content_hash == _approval_hash_for_entry(entry)
        )

    def _apply_loaded_entry_safety(
        self,
        entry: MemoryEntry,
        raw_data: dict[str, Any] | None = None,
        *,
        save_audit: bool = True,
    ) -> bool:
        """Safety-scan loaded entries and migrate legacy approval semantics."""
        raw_data = raw_data or {}
        scope = entry.scope
        safety = assess_memory_safety(entry.content, source=entry.source or "load")
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        previous_hash = entry.approval_content_hash
        changed = False

        raw_has_approval = "approval_status" in raw_data
        raw_has_hash = "approval_content_hash" in raw_data
        current_hash = _approval_hash_for_entry(entry)

        entry.safety_status = _normalize_safety_status(safety.status)
        entry.safety_reason = safety.reason

        if not raw_has_approval:
            if safety.status == _SAFETY_SAFE:
                entry.approval_status = (
                    _APPROVAL_APPROVED
                    if entry.lifecycle_status == _ACTIVE_LIFECYCLE
                    else _APPROVAL_PENDING
                )
            else:
                entry.approval_status = self._approval_for_safety(safety.status)
            entry.approval_reason = safety.reason or "legacy migration"
            entry.approval_actor = "migration"
            entry.approval_decided_at = time.time()
            changed = True
        elif safety.status == _SAFETY_UNSAFE:
            entry.approval_status = _APPROVAL_REJECTED
            entry.approval_reason = safety.reason
            entry.approval_actor = "safety_gate"
            entry.approval_decided_at = time.time()
            changed = True
        elif raw_has_hash and previous_hash and previous_hash != current_hash:
            entry.approval_status = _APPROVAL_PENDING
            entry.approval_reason = "approval hash mismatch after load"
            entry.approval_actor = "migration"
            entry.approval_decided_at = time.time()
            changed = True
        elif not raw_has_hash:
            if safety.status == _SAFETY_SUSPICIOUS and entry.approval_status != _APPROVAL_REJECTED:
                entry.approval_status = _APPROVAL_PENDING
                entry.approval_reason = safety.reason
                entry.approval_actor = "migration"
            changed = True

        if entry.approval_status == _APPROVAL_REJECTED:
            entry.lifecycle_status = "rejected"
            entry.curator_locked = False
        if entry.safety_status == _SAFETY_UNSAFE:
            entry.lifecycle_status = "rejected"
            entry.tier_reason = "safety_gate"

        if entry.approval_content_hash != current_hash:
            entry.approval_content_hash = current_hash
            changed = True
        if not entry.approval_actor:
            entry.approval_actor = "migration"
            changed = True
        if entry.approval_status not in _VALID_APPROVAL_STATUSES:
            entry.approval_status = _APPROVAL_PENDING
            changed = True

        if changed:
            self._append_approval_audit(
                scope,
                entry,
                action="migrate",
                actor=entry.approval_actor or "migration",
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=entry.approval_reason,
                safety=safety,
                extra={"had_approval": raw_has_approval, "had_hash": raw_has_hash},
                save=save_audit,
            )
        return changed
    
    def _load_scope(self, scope: MemoryScope) -> None:
        """Load memory file for a scope."""
        path = self._get_scope_path(scope)
        memory_md = path / "MEMORY.md"
        memory_json = path / "memory.json"
        
        if not memory_md.exists() and not memory_json.exists():
            return
        
        # Load JSON metadata if exists
        if memory_json.exists():
            try:
                raw_text = memory_json.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                
                is_valid, errors = _validate_memory_data(data)
                if is_valid:
                    changed = False
                    for entry_data in data.get("entries", []):
                        entry = MemoryEntry.from_dict(entry_data)
                        changed = self._apply_loaded_entry_safety(
                            entry,
                            entry_data,
                            save_audit=False,
                        ) or changed
                        self.memories[scope].entries.append(entry)
                    self.memories[scope]._rebuild_indices()
                    if changed and self._recover_on_load:
                        self._save_approval_audit(scope)
                        self._save_scope(scope)
                    return
                else:
                    logger.warning(
                        "Memory data validation failed for scope %s: %s",
                        scope.value,
                        "; ".join(errors[:5]),
                    )
                    if not self._recover_on_load:
                        raise MemoryStoreConflict("Memory authority is invalid")
                    valid_entries = _recover_entries(data, memory_json)
                    changed = False
                    for entry_data in valid_entries:
                        entry = MemoryEntry.from_dict(entry_data)
                        changed = self._apply_loaded_entry_safety(
                            entry,
                            entry_data,
                            save_audit=False,
                        ) or changed
                        self.memories[scope].entries.append(entry)
                    self.memories[scope]._rebuild_indices()
                    if valid_entries or changed:
                        if changed:
                            self._save_approval_audit(scope)
                        self._save_scope(scope)
                    return
            except json.JSONDecodeError as e:
                logger.error(
                    "JSON decode error in scope %s: %s", scope.value, e
                )
            except KeyError as e:
                logger.error(
                    "Missing key in scope %s data: %s", scope.value, e
                )
        
        # Load from MEMORY.md
        if memory_md.exists():
            content = memory_md.read_text(encoding="utf-8")
            self._parse_memory_md(content, scope)
            if self.memories[scope].entries and self._recover_on_load:
                self._save_scope(scope)
    
    def _parse_memory_md(self, content: str, scope: MemoryScope) -> None:
        """Parse MEMORY.md file into entries."""
        lines = content.split("\n")
        current_category = "general"
        entry_counter = 0
        
        for line in lines:
            line = line.strip()
            
            # Skip headers and metadata
            if line.startswith("#") or line.startswith("*") or not line:
                if line.startswith("## "):
                    current_category = line[3:].strip().lower()
                continue
            
            # Parse list items
            if line.startswith("- "):
                entry_content = line[2:]
                
                # Extract tags
                tags = []
                if "`" in entry_content:
                    import re
                    tag_matches = re.findall(r"`([^`]+)`", entry_content)
                    for tag_match in tag_matches:
                        tags.extend(tag_match.split())
                    entry_content = re.sub(r"`[^`]+`", "", entry_content).strip()
                
                entry_counter += 1
                entry = MemoryEntry(
                    id=f"{scope.value}-{entry_counter}",
                    scope=scope,
                    category=current_category,
                    content=entry_content,
                    tags=tags,
                )
                self._apply_loaded_entry_safety(entry, {})
                self.memories[scope].entries.append(entry)
        # Rebuild indices after Markdown-based loading
        if self.memories[scope].entries:
            self.memories[scope]._rebuild_indices()
    
    def _get_scope_path(self, scope: MemoryScope) -> Path:
        """Get path for memory scope."""
        if scope == MemoryScope.USER:
            return self.paths.user_memory
        elif scope == MemoryScope.PROJECT:
            return self.paths.project_memory
        else:
            return self.paths.local_memory
    
    _STORE_FILENAMES = ("memory.json", "MEMORY.md", "approval_audit.json")

    def _validate_scope_root(self, scope: MemoryScope) -> None:
        """Refuse a scope root that could redirect writes outside its owner.

        Without this a symlinked `.mini-code-memory` (or a symlinked store
        file inside it) silently relocates `memory.json`, `MEMORY.md`, and the
        approval audit anywhere on disk. The Dashboard approval authority
        already refuses exactly this shape; the plain manager is the same
        store and must not hold a weaker rule.
        """
        root = self._get_scope_path(scope)
        try:
            if root.is_symlink():
                raise MemoryStoreUnsafePath(
                    f"{scope.value} memory root is a symbolic link"
                )
            # PROJECT/LOCAL belong to this Workspace. USER legitimately lives
            # under the home data dir, so only the link check applies there.
            if scope in {MemoryScope.PROJECT, MemoryScope.LOCAL}:
                workspace = Path(self.workspace).resolve(strict=False)
                if root.parent.resolve(strict=False) != workspace:
                    raise MemoryStoreUnsafePath(
                        f"{scope.value} memory root escapes the workspace"
                    )
            for filename in self._STORE_FILENAMES:
                if (root / filename).is_symlink():
                    raise MemoryStoreUnsafePath(
                        f"{scope.value} memory file {filename} is a symbolic link"
                    )
        except OSError as error:
            raise MemoryStoreUnsafePath(
                f"{scope.value} memory root could not be validated"
            ) from error

    def _ensure_scope_path(self, scope: MemoryScope) -> None:
        """Ensure directory exists for scope."""
        path = self._get_scope_path(scope)
        # Validate before mkdir: mkdir(exist_ok=True) follows an existing
        # symlink, so checking afterwards would already have been too late.
        self._validate_scope_root(scope)
        path.mkdir(parents=True, exist_ok=True)
        self._validate_scope_root(scope)

    def _approval_audit_path(self, scope: MemoryScope) -> Path:
        return self._get_scope_path(scope) / "approval_audit.json"

    def _load_approval_audit(self, scope: MemoryScope) -> None:
        path = self._approval_audit_path(scope)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load approval audit for %s: %s", scope.value, exc)
            self.approval_audit[scope] = []
            return
        records = data.get("records", []) if isinstance(data, dict) else []
        self.approval_audit[scope] = [
            record for record in records if isinstance(record, dict)
        ]

    def _save_approval_audit(self, scope: MemoryScope) -> None:
        if not self.in_write_transaction:
            with self._store.transaction():
                expected = self._disk_revisions.get(scope)
                if expected is not None and expected != self._scope_disk_revision(scope):
                    raise MemoryStoreConflict("Memory approval audit changed")
                self._save_approval_audit(scope)
                self._disk_revisions[scope] = self._scope_disk_revision(scope)
                return
        self._ensure_scope_path(scope)
        path = self._approval_audit_path(scope)
        data = {
            "scope": scope.value,
            "last_updated": time.time(),
            "records": self.approval_audit.get(scope, []),
        }
        self._atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False, default=_json_default))

    def _append_approval_audit(
        self,
        scope: MemoryScope,
        entry: MemoryEntry,
        *,
        action: str,
        actor: str,
        previous_approval: str,
        previous_lifecycle: str,
        reason: str = "",
        safety: MemorySafetyResult | None = None,
        extra: dict[str, Any] | None = None,
        save: bool = True,
    ) -> dict[str, Any]:
        record = {
            "audit_id": f"audit-{time.time_ns()}-{uuid.uuid4().hex[:8]}",
            "entry_id": entry.id,
            "scope": scope.value,
            "action": action,
            "actor": actor,
            "created_at": time.time(),
            "previous_approval_status": previous_approval,
            "approval_status": entry.approval_status,
            "previous_lifecycle_status": previous_lifecycle,
            "lifecycle_status": entry.lifecycle_status,
            "safety_status": entry.safety_status,
            "safety_reason": _sanitize_untrusted_text(
                safety.reason if safety else entry.safety_reason,
                max_chars=240,
            ),
            "risk": safety.risk if safety else self._risk_for_safety(entry.safety_status),
            "content_hash": entry.approval_content_hash,
            "reason": _sanitize_untrusted_text(reason or entry.approval_reason, max_chars=240),
            "source": _sanitize_untrusted_text(entry.source, max_chars=120),
            "provenance": _redact_audit_value(entry.provenance),
            "extra": _redact_audit_value(extra or {}),
        }
        self.approval_audit.setdefault(scope, []).append(record)
        if save:
            self._save_approval_audit(scope)
        return record

    def get_approval_audit(self, entry_id: str) -> list[dict[str, Any]]:
        """Return persisted approval audit records for one memory id."""
        records: list[dict[str, Any]] = []
        for scope in MemoryScope:
            records.extend(
                record
                for record in self.approval_audit.get(scope, [])
                if record.get("entry_id") == entry_id
            )
        return sorted(records, key=lambda r: float(r.get("created_at", 0.0) or 0.0))
    
    @_coordinated_scope_write
    def add_entry(
        self,
        scope: MemoryScope,
        category: str = "auto",
        content: str = "",
        tags: list[str] | None = None,
        *,
        domains: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tier: MemoryTier | str | None = None,
        source: str = "",
        provenance: dict[str, Any] | None = None,
        lifecycle_status: str = _ACTIVE_LIFECYCLE,
        tier_reason: str = "",
        safety_status: str | None = None,
        safety_reason: str = "",
        allow_duplicate: bool = False,
        approval_policy: MemoryApprovalPolicy = MemoryApprovalPolicy.USER_EXPLICIT,
    ) -> MemoryEntry | None:
        """Add a new memory entry.

        If category is 'auto' or not provided, content will be automatically
        classified using keyword heuristics. This is the durable write boundary
        for memory: safety scanning, dedupe, full metadata construction, and
        persistence all happen before the entry becomes injectable.

        Args:
            scope: Memory scope level
            category: Category for the entry, or 'auto' for auto-classification
            content: Content of the memory entry
            tags: Optional list of tags

        Returns:
            The created or merged MemoryEntry, or None for empty content.
            Unsafe content is persisted as rejected so it can be audited but
            cannot be searched or injected.
        """
        scope = _coerce_scope(scope, MemoryScope.PROJECT)
        if not isinstance(approval_policy, MemoryApprovalPolicy):
            raise TypeError("approval_policy must be MemoryApprovalPolicy")
        if not content or not content.strip():
            return None

        self._ensure_scope_path(scope)

        final_category = category
        final_tags = list(tags or [])

        if category == "auto" and content:
            auto_category, auto_tags = _auto_classify_content(content)
            final_category = auto_category
            final_tags = list(dict.fromkeys(final_tags + auto_tags))

        safety = (
            MemorySafetyResult(
                _normalize_safety_status(safety_status),
                safety_reason,
                self._risk_for_safety(_normalize_safety_status(safety_status)),
            )
            if safety_status is not None
            else assess_memory_safety(content, source=source)
        )
        if safety.status == _SAFETY_UNSAFE:
            logger.warning(
                "Persisting rejected memory write scope=%s category=%s source=%s reason=%s",
                scope.value,
                final_category,
                source or "unspecified",
                safety.reason,
            )

        final_domains = list(dict.fromkeys(domains or []))
        final_metadata = dict(metadata or {})
        final_provenance = dict(provenance or {})
        final_tier = _coerce_tier(tier or MemoryTier.SHORT_TERM.value)

        if not allow_duplicate:
            duplicate = self._find_duplicate_entry(scope, content)
            if duplicate is not None:
                changed = False
                for tag in final_tags:
                    if tag not in duplicate.tags:
                        duplicate.tags.append(tag)
                        changed = True
                for domain in final_domains:
                    if domain not in duplicate.domains:
                        duplicate.domains.append(domain)
                        changed = True
                if final_metadata:
                    duplicate.metadata.update(final_metadata)
                    changed = True
                if final_provenance:
                    duplicate.provenance.update(final_provenance)
                    changed = True
                if source and not duplicate.source:
                    duplicate.source = source
                    changed = True
                if duplicate.tier != final_tier and final_tier != MemoryTier.SHORT_TERM:
                    duplicate.tier = final_tier
                    changed = True
                if changed:
                    previous_approval = duplicate.approval_status
                    previous_lifecycle = duplicate.lifecycle_status
                    if previous_approval == _APPROVAL_REJECTED or previous_lifecycle == "rejected":
                        duplicate.safety_status = safety.status
                        duplicate.safety_reason = safety.reason
                        duplicate.approval_status = _APPROVAL_REJECTED
                        duplicate.approval_reason = "previously rejected duplicate requires restore"
                        duplicate.approval_actor = "safety_gate"
                        duplicate.approval_decided_at = time.time()
                        duplicate.approval_content_hash = _approval_hash_for_entry(duplicate)
                        duplicate.lifecycle_status = "rejected"
                    else:
                        self._set_entry_safety_and_approval(
                            duplicate,
                            safety,
                            actor="safety_gate",
                            reason="duplicate merge",
                            lifecycle_status=lifecycle_status,
                            approval_policy=approval_policy,
                        )
                    duplicate.updated_at = time.time()
                    duplicate.invalidate_tokens()
                    self.memories[scope]._invalidate_cache()
                    self._append_approval_audit(
                        scope,
                        duplicate,
                        action="duplicate_merge",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason="metadata/source/provenance merge invalidated approval",
                        safety=safety,
                    )
                    self._save_scope(scope)
                return duplicate

        entry_id = f"{scope.value}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        entry = MemoryEntry(
            id=entry_id,
            scope=scope,
            category=final_category,
            content=content,
            tags=final_tags,
            domains=final_domains,
            tier=final_tier,
            metadata=final_metadata,
            source=source,
            provenance=final_provenance,
            lifecycle_status=lifecycle_status,
            tier_reason=tier_reason,
            approval_policy=approval_policy,
        )
        self._set_entry_safety_and_approval(
            entry,
            safety,
            actor="safety_gate",
            reason=(
                safety.reason
                or (
                    "automatic memory requires user review"
                    if approval_policy == MemoryApprovalPolicy.USER_REVIEW_REQUIRED
                    else "safe auto-approval"
                )
            ),
            lifecycle_status=lifecycle_status,
            approval_policy=approval_policy,
        )

        self.memories[scope].add_entry(entry)
        self._append_approval_audit(
            scope,
            entry,
            action="write",
            actor="safety_gate",
            previous_approval="new",
            previous_lifecycle="new",
            reason=entry.approval_reason,
            safety=safety,
        )
        self._save_scope(scope)
        return entry

    def _find_duplicate_entry(
        self, scope: MemoryScope, content: str
    ) -> MemoryEntry | None:
        """Find an exact or near-exact duplicate in one scope."""
        normalized = _normalize_memory_content(content)
        if not normalized:
            return None
        new_tokens = set(_tokenize(normalized))
        for entry in self.memories[scope].entries:
            existing = _normalize_memory_content(entry.content)
            if existing == normalized:
                return entry
            if not new_tokens:
                continue
            old_tokens = set(entry.get_tokens())
            if not old_tokens:
                continue
            union = new_tokens | old_tokens
            similarity = len(new_tokens & old_tokens) / len(union) if union else 0.0
            if similarity >= 0.95:
                return entry
        return None
    
    @_coordinated_scope_write
    def update_entry(self, scope: MemoryScope, entry_id: str, content: str) -> bool:
        """Update an existing entry through the same safety/dedupe boundary."""
        scope = _coerce_scope(scope, MemoryScope.PROJECT)
        if not content or not content.strip():
            return False
        memory_file = self.memories[scope]
        memory_file._ensure_cache_valid()
        entry = memory_file._id_index.get(entry_id)
        if entry is None:
            return False
        duplicate = self._find_duplicate_entry(scope, content)
        if duplicate is not None and duplicate.id != entry_id:
            logger.info(
                "Rejected memory update scope=%s entry_id=%s reason=duplicate_content",
                scope.value,
                entry_id,
            )
            return False
        safety = assess_memory_safety(content, source="update")
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        entry.content = content
        entry.updated_at = time.time()
        entry.invalidate_tokens()
        memory_file._invalidate_cache()
        self._set_entry_safety_and_approval(
            entry,
            safety,
            actor="safety_gate",
            reason="content update invalidated approval",
            lifecycle_status=_ACTIVE_LIFECYCLE,
            approval_policy=entry.approval_policy,
        )
        self._append_approval_audit(
            scope,
            entry,
            action="update",
            actor="safety_gate",
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
        )
        self._save_scope(scope)
        return True
    
    @_coordinated_scope_write
    def delete_entry(self, scope: MemoryScope, entry_id: str) -> bool:
        """Delete an entry."""
        if self.memories[scope].delete_entry(entry_id):
            self._save_scope(scope)
            return True
        return False

    @_coordinated_scope_write
    def add_tag(self, scope: MemoryScope, entry_id: str, tag: str) -> bool:
        """Add a tag to an entry."""
        for entry in self.memories[scope].entries:
            if entry.id == entry_id:
                if tag not in entry.tags:
                    previous_approval = entry.approval_status
                    previous_lifecycle = entry.lifecycle_status
                    entry.tags.append(tag)
                    safety = assess_memory_safety(entry.content, source="metadata")
                    if previous_approval != _APPROVAL_REJECTED and previous_lifecycle != "rejected":
                        self._set_entry_safety_and_approval(
                            entry,
                            safety,
                            actor="safety_gate",
                            reason="tag metadata changed",
                            lifecycle_status=entry.lifecycle_status,
                            approval_policy=entry.approval_policy,
                        )
                    else:
                        entry.safety_status = safety.status
                        entry.safety_reason = safety.reason
                        entry.approval_content_hash = _approval_hash_for_entry(entry)
                    entry.invalidate_tokens()
                    self.memories[scope]._invalidate_cache()
                    self._append_approval_audit(
                        scope,
                        entry,
                        action="metadata_update",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason="tag metadata changed",
                        safety=safety,
                    )
                    self._save_scope(scope)
                return True
        return False

    @_coordinated_scope_write
    def remove_tag(self, scope: MemoryScope, entry_id: str, tag: str) -> bool:
        """Remove a tag from an entry."""
        for entry in self.memories[scope].entries:
            if entry.id == entry_id:
                if tag in entry.tags:
                    previous_approval = entry.approval_status
                    previous_lifecycle = entry.lifecycle_status
                    entry.tags.remove(tag)
                    safety = assess_memory_safety(entry.content, source="metadata")
                    if previous_approval != _APPROVAL_REJECTED and previous_lifecycle != "rejected":
                        self._set_entry_safety_and_approval(
                            entry,
                            safety,
                            actor="safety_gate",
                            reason="tag metadata changed",
                            lifecycle_status=entry.lifecycle_status,
                            approval_policy=entry.approval_policy,
                        )
                    else:
                        entry.safety_status = safety.status
                        entry.safety_reason = safety.reason
                        entry.approval_content_hash = _approval_hash_for_entry(entry)
                    entry.invalidate_tokens()
                    self.memories[scope]._invalidate_cache()
                    self._append_approval_audit(
                        scope,
                        entry,
                        action="metadata_update",
                        actor="safety_gate",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason="tag metadata changed",
                        safety=safety,
                    )
                    self._save_scope(scope)
                return True
        return False

    def search_by_tag(self, scope: MemoryScope, tag: str) -> list[MemoryEntry]:
        """Search entries by tag."""
        return [
            entry for entry in self.memories[scope].entries
            if tag in entry.tags and entry.is_active
        ]

    def get_all_tags(self, scope: MemoryScope) -> set[str]:
        """Get all unique tags in a scope."""
        tags: set[str] = set()
        for entry in self.memories[scope].entries:
            tags.update(entry.tags)
        return tags

    def get_tags_by_category(self, scope: MemoryScope) -> dict[str, list[str]]:
        """Get tags grouped by category."""
        category_tags: dict[str, set[str]] = {}
        for entry in self.memories[scope].entries:
            if entry.category not in category_tags:
                category_tags[entry.category] = set()
            category_tags[entry.category].update(entry.tags)
        return {cat: sorted(list(tags)) for cat, tags in category_tags.items()}

    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        limit: int = 20,
        min_relevance: float = 0.1,
        active_domains: list[str] | None = None,
        *,
        record_usage: bool = True,
    ) -> list[MemoryEntry]:
        """Search across memory scopes with TF-IDF + domain relevance.

        Args:
            query: Search query string
            scope: Optional scope to limit search to
            limit: Maximum results to return
            min_relevance: Minimum relevance score threshold (0.0-1.0)
            active_domains: Current domain context for soft boosting

        Returns:
            Entries ranked by relevance (TF-IDF + domain + usage + recency)
        """
        if record_usage and not self.in_write_transaction:
            coordinated_scopes = (
                (_coerce_scope(scope, MemoryScope.PROJECT),)
                if scope is not None
                else tuple(MemoryScope)
            )
            return self.coordinated_write(
                coordinated_scopes,
                lambda: self.search(
                    query,
                    scope=scope,
                    limit=limit,
                    min_relevance=min_relevance,
                    active_domains=active_domains,
                    record_usage=record_usage,
                ),
            )

        results = []

        if scope is not None:
            scope = _coerce_scope(scope, MemoryScope.PROJECT)
        scopes_to_search = [scope] if scope else list(MemoryScope)

        for s in scopes_to_search:
            scoped_results = self.memories[s].search(
                query,
                active_domains=active_domains,
                record_usage=record_usage,
            )
            if scoped_results and record_usage:
                self._save_scope(s)
            results.extend(scoped_results)

        # Apply minimum relevance threshold
        # (entries are already scored by MemoryFile.search)
        if min_relevance > 0:
            if results:
                max_score = max(getattr(e, "_last_relevance", 0.0) for e in results)
                if max_score > 0:
                    results = [
                        e for e in results
                        if getattr(e, "_last_relevance", 0.0) / max_score >= min_relevance
                    ]

        ranked = sorted(
            results,
            key=lambda e: (
                -self._global_rank(e, active_domains=active_domains),
                -e.updated_at,
                e.scope.value,
                e.id,
            ),
        )

        # Deduplicate by normalized content after global ranking.
        seen_content: set[str] = set()
        deduped = []
        for entry in ranked:
            content_key = _normalize_memory_content(entry.content)[:200]
            if content_key not in seen_content:
                seen_content.add(content_key)
                deduped.append(entry)

        return deduped[:limit]

    def _global_rank(
        self,
        entry: MemoryEntry,
        active_domains: list[str] | None = None,
    ) -> float:
        """Rank results across scopes deterministically after per-file scoring."""
        scope_priority = {
            MemoryScope.LOCAL: 0.25,
            MemoryScope.PROJECT: 0.18,
            MemoryScope.USER: 0.10,
        }.get(entry.scope, 0.0)
        tier_priority = {
            MemoryTier.WORKING: 0.05,
            MemoryTier.SHORT_TERM: 0.08,
            MemoryTier.LONG_TERM: 0.12,
            MemoryTier.ARCHIVAL: -1.0,
        }.get(entry.tier, 0.0)
        domain_boost = 0.0
        if active_domains and entry.domains:
            active = set(active_domains)
            entry_domains = set(entry.domains)
            union = active | entry_domains
            domain_boost = (len(active & entry_domains) / len(union)) * 0.25 if union else 0.0
        age_hours = (time.time() - entry.updated_at) / 3600
        recency = 1.0 / (1.0 + age_hours / 24.0) * 0.10
        feedback = entry.usefulness_score * 0.25
        raw_relevance = getattr(entry, "_last_relevance", 0.0)
        return raw_relevance + scope_priority + tier_priority + domain_boost + recency + feedback

    def _score_entry(self, entry: MemoryEntry, query_tokens: list[str]) -> float:
        """Compute relevance score for a memory entry."""
        if not query_tokens:
            return 0.0

        query_tokens_expanded = _expand_query_terms(query_tokens)
        entry_tokens = _tokenize(
            f"{entry.content} {entry.category} {' '.join(entry.tags)}"
        )
        idf = _compute_idf([entry_tokens])
        avgdl = len(entry_tokens)
        bm25 = _bm25_score(query_tokens_expanded, entry_tokens, idf, avgdl)

        query_lower = " ".join(query_tokens).lower()
        content_lower = entry.content.lower()
        substring_score = 0.0
        if query_lower in content_lower:
            substring_score = 2.0
        elif any(q in content_lower for q in query_tokens):
            substring_score = 1.0

        tag_score = 0.0
        exact_tag_match = any(tag.lower() == query_lower for tag in entry.tags)
        partial_tag_match = any(query_lower in tag.lower() for tag in entry.tags)
        if exact_tag_match:
            tag_score = 5.0
        elif partial_tag_match:
            tag_score = 1.5
        if query_lower in entry.category.lower():
            tag_score += 1.0

        usage_bonus = math.log1p(entry.usage_count) * 0.3

        age_hours = (time.time() - entry.updated_at) / 3600
        recency_bonus = 1.0 / (1.0 + age_hours / 24.0) * 0.5

        return bm25 + substring_score + tag_score + usage_bonus + recency_bonus
    
    def get_relevant_context(
        self,
        max_entries: int = 20,
        max_tokens: int = 8000,
        query: str | None = None,
        *,
        current_files: list[str] | None = None,
        active_domains: list[str] | None = None,
        context_usage: float = 0.0,
        max_tokens_per_memory: int | None = None,
        min_relevance: float = 0.0,
    ) -> str:
        """Get relevant memory context for system prompt injection.
        
        Returns formatted MEMORY.md content from all scopes,
        respecting token limits.
        """
        from minicode.context_manager import estimate_tokens

        query = (query or "").strip()
        if query:
            from minicode.memory_retrieval import (
                CanonicalMemoryRetriever,
                MemoryRetrievalRequest,
                RetrievalSource,
            )

            result = CanonicalMemoryRetriever(self).retrieve(
                MemoryRetrievalRequest(
                    query=query,
                    current_files=tuple(current_files or ()),
                    active_domains=tuple(active_domains or ()),
                    context_usage=context_usage,
                    max_memories=max_entries,
                    max_total_tokens=max_tokens,
                    max_tokens_per_memory=(
                        max_tokens_per_memory
                        if max_tokens_per_memory is not None
                        else max_tokens
                    ),
                    min_relevance=min_relevance,
                    source_entrypoint=RetrievalSource.MANAGER_CONTEXT,
                )
            )
            self._last_retrieval_result = result
            self.record_retrievals(list(result.selected_ids))
            return result.prompt_text
        
        parts = []
        total_tokens = 0
        
        # Priority order: LOCAL > PROJECT > USER
        for scope in [MemoryScope.LOCAL, MemoryScope.PROJECT, MemoryScope.USER]:
            memory = self.memories[scope]
            active_entries = [entry for entry in memory.entries if entry.is_active]
            if not active_entries:
                continue
            
            formatted = MemoryFile(scope=scope, entries=active_entries).format_as_markdown(include_header=True)
            tokens = estimate_tokens(formatted)
            
            if total_tokens + tokens <= max_tokens:
                parts.append(formatted)
                total_tokens += tokens
            else:
                # Partial: include only recent entries
                remaining_tokens = max_tokens - total_tokens
                partial_entries = active_entries[-max_entries:]
                partial_memory = MemoryFile(scope=scope, entries=partial_entries)
                formatted = partial_memory.format_as_markdown(include_header=True)
                
                if estimate_tokens(formatted) <= remaining_tokens:
                    parts.append(formatted)
                break
        
        if not parts:
            return ""
        
        return "\n\n".join(parts)

    def _find_entry_by_id(
        self, entry_id: str
    ) -> tuple[MemoryScope, MemoryEntry] | tuple[None, None]:
        for scope in MemoryScope:
            memory = self.memories.get(scope)
            if not memory:
                continue
            memory._ensure_cache_valid()
            entry = memory._id_index.get(entry_id)
            if entry is not None:
                return scope, entry
        return None, None

    @_coordinated_all_write
    def record_injections(self, entry_ids: list[str]) -> None:
        """Persist that specific memories were actually injected."""
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug("Memory injection feedback skipped missing entry_id=%s", entry_id)
                continue
            entry.injection_count += 1
            entry.last_used = now
            entry.last_accessed = now
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    @_coordinated_all_write
    def record_retrievals(self, entry_ids: list[str]) -> None:
        """Persist one retrieval observation per selected entry and scope."""
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug("Memory retrieval skipped missing entry_id=%s", entry_id)
                continue
            entry.retrieval_count += 1
            entry.last_accessed = now
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    @_coordinated_all_write
    def record_retrievals_and_injections(
        self,
        retrieved_entry_ids: list[str],
        injected_entry_ids: list[str],
    ) -> None:
        """Batch task-start counters so each touched scope is saved once."""
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(retrieved_entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug("Memory retrieval skipped missing entry_id=%s", entry_id)
                continue
            entry.retrieval_count += 1
            entry.last_accessed = now
            touched.add(scope)
        for entry_id in dict.fromkeys(injected_entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug("Memory injection skipped missing entry_id=%s", entry_id)
                continue
            entry.injection_count += 1
            entry.last_used = now
            entry.last_accessed = now
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    @_coordinated_all_write
    def record_feedback(self, entry_ids: list[str], success: bool) -> None:
        """Persist task-outcome feedback for memories that were injected."""
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug("Memory outcome feedback skipped missing entry_id=%s", entry_id)
                continue
            if success:
                entry.success_count += 1
                entry.usage_count += 1
            else:
                entry.failure_count += 1
            entry.last_used = now
            entry.last_accessed = now
            total = entry.success_count + entry.failure_count
            entry.usefulness_score = (
                (entry.success_count - entry.failure_count) / total
                if total
                else 0.0
            )
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    @_coordinated_all_write
    def record_corroborated_feedback(self, entry_ids: list[str], success: bool) -> None:
        """Persist feedback backed by independent verification or an explicit
        user accept/correct/reject signal, kept separate from whole-turn
        outcome feedback because it carries materially stronger evidence.
        """
        touched: set[MemoryScope] = set()
        now = time.time()
        for entry_id in dict.fromkeys(entry_ids):
            scope, entry = self._find_entry_by_id(entry_id)
            if scope is None or entry is None:
                logger.debug(
                    "Memory corroborated feedback skipped missing entry_id=%s", entry_id
                )
                continue
            if success:
                entry.corroborated_success_count += 1
            else:
                entry.corroborated_failure_count += 1
            entry.last_used = now
            entry.last_accessed = now
            total = entry.corroborated_success_count + entry.corroborated_failure_count
            entry.corroborated_usefulness_score = (
                (entry.corroborated_success_count - entry.corroborated_failure_count) / total
                if total
                else 0.0
            )
            touched.add(scope)
        for scope in sorted(touched, key=lambda item: item.value):
            self._save_scope(scope)

    def _save_scope(self, scope: MemoryScope) -> None:
        """Save memory to disk (atomic write to prevent corruption)."""
        if not self.in_write_transaction:
            with self._store.transaction():
                expected = self._disk_revisions.get(scope)
                if expected is not None and expected != self._scope_disk_revision(scope):
                    raise MemoryStoreConflict("Memory scope changed")
                self._save_scope_locked(scope)
                return
        self._save_scope_locked(scope)

    def _save_scope_locked(self, scope: MemoryScope) -> None:
        """Write one scope while the caller owns the cooperative transaction."""
        path = self._get_scope_path(scope)
        self._ensure_scope_path(scope)
        
        # Save JSON metadata (atomic: write to temp, then replace)
        memory_json = path / "memory.json"
        data = {
            "scope": scope.value,
            "last_updated": time.time(),
            "entries": [e.to_dict() for e in self.memories[scope].entries],
        }
        self._atomic_write(memory_json, json.dumps(data, indent=2, ensure_ascii=False))
        
        # Also update MEMORY.md for human readability (atomic)
        memory_md = path / "MEMORY.md"
        self._atomic_write(memory_md, self.memories[scope].format_as_markdown())
        self._disk_revisions[scope] = self._scope_disk_revision(scope)
    
    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """Write content atomically: write to temp file, then os.replace().
        
        This prevents data corruption if the process is killed mid-write
        or if multiple instances write to the same file concurrently.

        Also refuses a symlinked target or parent as defense in depth: every
        store write funnels through here, so a caller that skipped scope-root
        validation still cannot redirect the write off-target.
        """
        import tempfile
        try:
            if target.is_symlink() or target.parent.is_symlink():
                raise MemoryStoreUnsafePath(f"{target.name} target is a symbolic link")
        except OSError as error:
            raise MemoryStoreUnsafePath(
                f"{target.name} target could not be validated"
            ) from error
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(target))
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    
    def get_stats(self) -> dict[str, Any]:
        """Get memory statistics."""
        return {
            scope.value: {
                "entries": len(memory.entries),
                "size_bytes": memory.size_bytes,
                "categories": list(set(e.category for e in memory.entries)),
            }
            for scope, memory in self.memories.items()
        }
    
    def format_stats(self) -> str:
        """Format memory stats for display with tier and domain breakdown."""
        from collections import Counter

        lines = ["Memory System Status", "=" * 50, ""]
        tiers: Counter[str] = Counter()
        domains: Counter[str] = Counter()
        approvals: Counter[str] = Counter()
        total_entries = 0
        total_size = 0
        insight_count = 0

        for scope_name, scope_stats in self.get_stats().items():
            lines.append(f"{scope_name.title()}: {scope_stats['entries']} entries, "
                        f"{scope_stats['size_bytes'] / 1024:.1f} KB")
            total_entries += scope_stats["entries"]
            total_size += scope_stats["size_bytes"]

            # Collect tier and domain stats
            scope = MemoryScope(scope_name)
            if scope in self.memories:
                for e in self.memories[scope].entries:
                    tiers[e.tier.value] += 1
                    approvals[e.approval_status] += 1
                    for d in e.domains:
                        domains[d] += 1
                    if e.category == "insight":
                        insight_count += 1

        lines.append("")
        lines.append(f"Total: {total_entries} entries ({total_size / 1024:.1f} KB)")
        if approvals.get(_APPROVAL_PENDING, 0):
            lines.append(
                f"Pending Approval: {approvals[_APPROVAL_PENDING]} "
                "(use /memory pending)"
            )
        lines.append("")

        if approvals:
            lines.append("Approval Distribution:")
            for status in [_APPROVAL_APPROVED, _APPROVAL_PENDING, _APPROVAL_REJECTED]:
                lines.append(f"  {status:<12} {approvals.get(status, 0):>4}")
            lines.append("")

        if tiers:
            lines.append("Tier Distribution:")
            for tier_name in ["working", "short_term", "long_term", "archival"]:
                count = tiers.get(tier_name, 0)
                bar = "#" * (count // max(1, total_entries // 20))
                lines.append(f"  {tier_name:<12} {count:>4} {bar}")
            lines.append("")

        if domains:
            lines.append("Domain Distribution:")
            for domain, count in domains.most_common(6):
                lines.append(f"  {domain:<15} {count:>3}")
            lines.append("")

        if insight_count:
            lines.append(f"Curator Insights: {insight_count} synthesized")

        return "\n".join(lines)

    def pending_entries(self, scope: MemoryScope | None = None) -> list[MemoryEntry]:
        scopes = [_coerce_scope(scope)] if scope is not None else list(MemoryScope)
        entries: list[MemoryEntry] = []
        for scoped in scopes:
            entries.extend(
                entry
                for entry in self.memories[scoped].entries
                if entry.approval_status == _APPROVAL_PENDING
            )
        return sorted(entries, key=lambda e: (e.created_at, e.scope.value, e.id))

    def _format_pending_entries(self, scope: MemoryScope | None = None) -> str:
        entries = self.pending_entries(scope)
        if not entries:
            return "No pending memory approvals."
        lines = ["Pending Memory Approvals", "=" * 50]
        for entry in entries:
            risk = self._risk_for_safety(entry.safety_status)
            preview = _sanitize_untrusted_text(entry.content, max_chars=160).replace("\n", "\\n")
            lines.append(
                f"- {entry.id} [{entry.scope.value}] source={entry.source or 'unknown'} "
                f"created={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry.created_at))} "
                f"risk={risk} reason={_sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, 120)}"
            )
            provenance = _redact_audit_value(entry.provenance)
            if provenance:
                lines.append(f"  provenance={_sanitize_untrusted_text(json.dumps(provenance, ensure_ascii=False, default=_json_default), 180)}")
            lines.append(f"  summary={preview}")
        return "\n".join(lines)

    def _format_memory_review(self, entry_id: str) -> str:
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        audit_count = len(self.get_approval_audit(entry.id))
        lines = [
            f"Memory Review: {entry.id}",
            "=" * 50,
            f"scope: {scope.value}",
            f"approval: {entry.approval_status}",
            f"safety: {entry.safety_status}",
            f"lifecycle: {entry.lifecycle_status}",
            f"tier: {entry.tier.value}",
            f"source: {entry.source or 'unknown'}",
            f"risk: {self._risk_for_safety(entry.safety_status)}",
            f"reason: {_sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, 240)}",
            f"hash: {entry.approval_content_hash or '[missing]'}",
            f"audit_records: {audit_count}",
            "",
            "Untrusted Content:",
            _sanitize_untrusted_text(entry.content, max_chars=1200),
        ]
        return "\n".join(lines)

    def decide_pending_entry(
        self,
        entry_id: str,
        decision: str,
        *,
        actor: str,
        reason: str,
    ) -> MemoryApprovalMutation:
        """Apply one typed decision and return state, never a parsed message."""
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid Memory decision")
        scope, before = self._find_entry_by_id(entry_id)
        if scope is None or before is None:
            raise KeyError(entry_id)
        if before.approval_status != _APPROVAL_PENDING:
            previous_status = before.approval_status
            if decision == "approve":
                message = (
                    f"Cannot approve memory {before.id}: "
                    f"status={before.approval_status}; only pending memories can be approved."
                )
            elif before.approval_status == _APPROVAL_REJECTED:
                message = f"Memory {before.id} is already rejected."
            else:
                message = self.reject_entry(entry_id, actor=actor, reason=reason)
            _, current = self._find_entry_by_id(entry_id)
            current = current or before
            return MemoryApprovalMutation(
                memory_id=before.id,
                scope=scope,
                status=current.approval_status,
                decision=decision,
                decision_accepted=(
                    decision == "reject"
                    and previous_status == _APPROVAL_APPROVED
                    and current.approval_status == _APPROVAL_REJECTED
                ),
                updated_at=current.updated_at,
                compatibility_message=message,
            )
        if decision == "approve":
            message = self.approve_entry(entry_id, actor=actor, reason=reason)
        else:
            message = self.reject_entry(entry_id, actor=actor, reason=reason)
        final_scope, final = self._find_entry_by_id(entry_id)
        if final_scope is None or final is None:
            raise MemoryStoreConflict("Memory disappeared during decision")
        return MemoryApprovalMutation(
            memory_id=final.id,
            scope=final_scope,
            status=final.approval_status,
            decision=decision,
            decision_accepted=(
                final.approval_status
                == (_APPROVAL_APPROVED if decision == "approve" else _APPROVAL_REJECTED)
            ),
            updated_at=final.updated_at,
            compatibility_message=message,
        )

    def approve_entry(
        self,
        entry_id: str,
        *,
        actor: str = "user",
        reason: str = "",
    ) -> str:
        if not self.in_write_transaction:
            try:
                return self.coordinated_write(
                    tuple(MemoryScope),
                    lambda: self.approve_entry(entry_id, actor=actor, reason=reason),
                    reject_stale=True,
                )
            except MemoryStoreConflict:
                return (
                    f"Approval blocked for memory {entry_id}: content hash changed; "
                    "review it again."
                )
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        if entry.approval_status != _APPROVAL_PENDING:
            return (
                f"Cannot approve memory {entry.id}: status={entry.approval_status}; "
                "only pending memories can be approved."
            )
        if entry.curator_locked or entry.lifecycle_status != _ACTIVE_LIFECYCLE or entry.tier == MemoryTier.ARCHIVAL:
            return f"Cannot approve memory {entry.id} directly; use /memory restore {entry.id}."

        current_hash = _approval_hash_for_entry(entry)
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        if entry.approval_content_hash != current_hash:
            safety = assess_memory_safety(entry.content, source="approve")
            self._set_entry_safety_and_approval(
                entry,
                safety,
                actor="safety_gate",
                reason="approval hash mismatch; review current content",
                lifecycle_status=_ACTIVE_LIFECYCLE,
            )
            if safety.status == _SAFETY_SAFE:
                entry.approval_status = _APPROVAL_PENDING
                entry.approval_reason = "approval hash mismatch; review current content"
                entry.approval_actor = "safety_gate"
                entry.approval_decided_at = time.time()
            entry.approval_content_hash = current_hash
            self._append_approval_audit(
                scope,
                entry,
                action="approval_hash_mismatch",
                actor=actor,
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=entry.approval_reason,
                safety=safety,
            )
            self._save_scope(scope)
            return f"Approval blocked for memory {entry.id}: content hash changed; review it again."

        safety = assess_memory_safety(entry.content, source="approve")
        entry.safety_status = safety.status
        entry.safety_reason = safety.reason
        if safety.status == _SAFETY_UNSAFE:
            entry.approval_status = _APPROVAL_REJECTED
            entry.lifecycle_status = "rejected"
            entry.approval_reason = safety.reason
            entry.approval_actor = "safety_gate"
            entry.approval_decided_at = time.time()
            entry.approval_content_hash = current_hash
            self._append_approval_audit(
                scope,
                entry,
                action="approve_rejected_by_safety",
                actor=actor,
                previous_approval=previous_approval,
                previous_lifecycle=previous_lifecycle,
                reason=safety.reason,
                safety=safety,
            )
            self._save_scope(scope)
            return f"Cannot approve memory {entry.id}: safety_status=unsafe reason={safety.reason}"

        entry.approval_status = _APPROVAL_APPROVED
        entry.approval_reason = reason or "approved by user"
        entry.approval_actor = actor
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = current_hash
        entry.lifecycle_status = _ACTIVE_LIFECYCLE
        entry.updated_at = time.time()
        self._append_approval_audit(
            scope,
            entry,
            action="approve",
            actor=actor,
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
        )
        self._save_scope(scope)
        return f"Approved memory {entry.id}."

    def reject_entry(self, entry_id: str, *, actor: str = "user", reason: str = "") -> str:
        if not self.in_write_transaction:
            try:
                return self.coordinated_write(
                    tuple(MemoryScope),
                    lambda: self.reject_entry(entry_id, actor=actor, reason=reason),
                    reject_stale=True,
                )
            except MemoryStoreConflict:
                return (
                    f"Rejection blocked for memory {entry_id}: content hash changed; "
                    "review it again."
                )
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        if entry.approval_status == _APPROVAL_REJECTED and entry.lifecycle_status == "rejected":
            return f"Memory {entry.id} is already rejected."
        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        safety = assess_memory_safety(entry.content, source="reject")
        entry.safety_status = safety.status
        entry.safety_reason = safety.reason
        entry.approval_status = _APPROVAL_REJECTED
        entry.approval_reason = reason or "rejected by user"
        entry.approval_actor = actor
        entry.approval_decided_at = time.time()
        entry.approval_content_hash = _approval_hash_for_entry(entry)
        entry.lifecycle_status = "rejected"
        entry.curator_locked = False
        entry.updated_at = time.time()
        self._append_approval_audit(
            scope,
            entry,
            action="reject",
            actor=actor,
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
        )
        self._save_scope(scope)
        return f"Rejected memory {entry.id}."

    def _entry_reference_warnings(self, entry: MemoryEntry) -> list[str]:
        candidates = re.findall(r"(?<![\w/.-])([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})(?![\w/.-])", entry.content)
        warnings: list[str] = []
        workspace_path = Path(self.workspace)
        for candidate in dict.fromkeys(candidates):
            if candidate.startswith(("http://", "https://")):
                continue
            path = Path(candidate)
            if not path.is_absolute():
                path = workspace_path / candidate
            if not path.exists():
                warnings.append(candidate)
        return warnings[:10]

    @_coordinated_all_write
    def restore_entry(self, entry_id: str, *, actor: str = "user") -> str:
        scope, entry = self._find_entry_by_id(entry_id)
        if scope is None or entry is None:
            return f"Memory not found: {entry_id}"
        if entry.is_active:
            return f"Memory {entry.id} is already active and approved."

        previous_approval = entry.approval_status
        previous_lifecycle = entry.lifecycle_status
        safety = assess_memory_safety(entry.content, source="restore")
        missing_refs = self._entry_reference_warnings(entry)
        if missing_refs and safety.status == _SAFETY_SAFE:
            safety = MemorySafetyResult(
                _SAFETY_SUSPICIOUS,
                "referenced files are missing: " + ", ".join(missing_refs),
                "medium",
            )

        self._set_entry_safety_and_approval(
            entry,
            safety,
            actor="safety_gate",
            reason="restore revalidation",
            lifecycle_status=_ACTIVE_LIFECYCLE,
        )
        if safety.status != _SAFETY_UNSAFE:
            entry.lifecycle_status = _ACTIVE_LIFECYCLE
            entry.curator_locked = False
            entry.deprecated_at = None
            if entry.tier == MemoryTier.ARCHIVAL:
                entry.tier = MemoryTier.SHORT_TERM
                entry.tier_reason = "restored"
        self._append_approval_audit(
            scope,
            entry,
            action="restore",
            actor=actor,
            previous_approval=previous_approval,
            previous_lifecycle=previous_lifecycle,
            reason=entry.approval_reason,
            safety=safety,
            extra={"missing_references": missing_refs},
        )
        self._save_scope(scope)
        if entry.approval_status == _APPROVAL_APPROVED:
            return f"Restored and approved memory {entry.id}."
        if entry.approval_status == _APPROVAL_PENDING:
            return f"Restored memory {entry.id} to pending approval: {entry.safety_reason}"
        return f"Restore rejected memory {entry.id}: {entry.safety_reason}"
    
    @_coordinated_scope_write
    def clear_scope(self, scope: MemoryScope) -> None:
        """Clear all entries in a scope."""
        self.memories[scope] = MemoryFile(scope=scope)
        self._save_scope(scope)

    def handle_user_memory_input(self, user_input: str) -> str | None:
        """Handle explicit memory inputs from the main chat path.

        Supported forms:
        - "# remember this project convention"
        - "/memory add remember this project convention"
        - "/memory add project: remember this shared project convention"
        - "/memory add local: remember this local-only note"
        - "/memory add user: remember this cross-project preference"
        """
        raw = user_input.strip()
        if not raw:
            return None

        content = ""
        scope = MemoryScope.PROJECT
        category = "note"

        if raw == "/memory":
            return self.format_stats()
        if raw.startswith("/memory pending"):
            scope_filter: MemoryScope | None = None
            scope_match = re.search(r"--scope\s+(\w+)", raw, flags=re.I)
            if scope_match:
                try:
                    scope_filter = MemoryScope(scope_match.group(1).lower())
                except ValueError:
                    return "Usage: /memory pending [--scope USER|PROJECT|LOCAL]"
            return self._format_pending_entries(scope_filter)
        if raw.startswith("/memory review "):
            entry_id = raw[len("/memory review ") :].strip()
            if not entry_id:
                return "Usage: /memory review <entry_id>"
            return self._format_memory_review(entry_id)
        if raw.startswith("/memory approve "):
            entry_id = raw[len("/memory approve ") :].strip()
            if not entry_id:
                return "Usage: /memory approve <entry_id>"
            try:
                return self.decide_pending_entry(
                    entry_id,
                    "approve",
                    actor="user",
                    reason="",
                ).compatibility_message
            except KeyError:
                return f"Memory not found: {entry_id}"
        if raw.startswith("/memory reject "):
            rest = raw[len("/memory reject ") :].strip()
            if not rest:
                return "Usage: /memory reject <entry_id>"
            entry_id, _, reason = rest.partition(" ")
            try:
                return self.decide_pending_entry(
                    entry_id,
                    "reject",
                    actor="user",
                    reason=reason.strip(),
                ).compatibility_message
            except KeyError:
                return f"Memory not found: {entry_id}"
        if raw.startswith("/memory restore "):
            entry_id = raw[len("/memory restore ") :].strip()
            if not entry_id:
                return "Usage: /memory restore <entry_id>"
            return self.restore_entry(entry_id)

        if raw.startswith("#"):
            content = raw[1:].strip()
            category = "directive"
        elif raw.startswith("/memory add "):
            content = raw[len("/memory add ") :].strip()
            scope_match = re.match(r"^(user|project|local)\s*:\s*(.+)$", content, flags=re.I)
            if scope_match:
                scope = MemoryScope(scope_match.group(1).lower())
                content = scope_match.group(2).strip()
        else:
            return None

        if not content:
            return "Usage: # <memory> or /memory add [user|project|local:] <memory>"

        entry = self.add_entry(scope, category, content, tags=["chat"])
        if entry is None:
            return "Usage: # <memory> or /memory add [user|project|local:] <memory>"
        if entry.approval_status == _APPROVAL_PENDING:
            reason = _sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, max_chars=160)
            return (
                f"Memory pending approval ({entry.scope.value}): {entry.id} reason={reason}. "
                f"Use /memory review {entry.id} before /memory approve {entry.id} or /memory reject {entry.id}."
            )
        if entry.approval_status == _APPROVAL_REJECTED:
            reason = _sanitize_untrusted_text(entry.safety_reason or entry.approval_reason, max_chars=160)
            return f"Rejected memory ({entry.scope.value}): {entry.id} reason={reason}"
        return f"Saved memory ({entry.scope.value}): {entry.content}"

    def check_integrity(self, scope: MemoryScope) -> dict[str, Any]:
        """Validate all entries in a scope for integrity.

        Checks:
        - Valid IDs (non-empty strings)
        - Valid categories (non-empty strings)
        - Non-empty content
        - No duplicate IDs

        Args:
            scope: Memory scope to check

        Returns:
            Dictionary with {is_valid: bool, issues: list[str]}
        """
        issues: list[str] = []
        seen_ids: set[str] = set()
        entries = self.memories[scope].entries

        for idx, entry in enumerate(entries):
            if not entry.id or not isinstance(entry.id, str):
                issues.append(
                    f"Entry at index {idx} has invalid or empty ID"
                )

            if entry.id in seen_ids:
                issues.append(
                    f"Duplicate ID found: '{entry.id}' "
                    f"(entries {list(self._find_entry_indices(scope, entry.id))})"
                )
            else:
                seen_ids.add(entry.id)

            if not entry.category or not isinstance(entry.category, str):
                issues.append(
                    f"Entry '{entry.id}' has invalid or empty category"
                )

            if not entry.content or not isinstance(entry.content, str):
                issues.append(
                    f"Entry '{entry.id}' has empty or invalid content"
                )

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
        }

    @_coordinated_scope_write
    def compress_scope(
        self, scope: MemoryScope, similarity_threshold: float = 0.8
    ) -> dict[str, int]:
        """Compress memory entries by merging similar content.

        Merges entries with content similarity above the threshold.
        Removes duplicate entries (exact content matches).
        Updates timestamps and preserves usage counts.

        Args:
            scope: Memory scope to compress
            similarity_threshold: Jaccard similarity threshold for merging
                (default 0.8 = 80%)

        Returns:
            Stats dictionary with {merged_count, removed_count, remaining_count}
        """
        entries = self.memories[scope].entries
        if len(entries) <= 1:
            return {"merged_count": 0, "removed_count": 0, "remaining_count": len(entries)}

        seen_content: dict[str, int] = {}
        duplicates_removed = 0

        unique_entries = []
        for entry in entries:
            content_key = entry.content.strip().lower()
            if content_key in seen_content:
                master_idx = seen_content[content_key]
                master = unique_entries[master_idx]
                master.usage_count += entry.usage_count
                master.updated_at = max(master.updated_at, entry.updated_at)
                master.tags = sorted(
                    list(set(master.tags + entry.tags))
                )
                duplicates_removed += 1
            else:
                seen_content[content_key] = len(unique_entries)
                unique_entries.append(entry)

        merged_count = 0
        final_entries: list[MemoryEntry] = []
        merged_indices: set[int] = set()

        for i, entry_a in enumerate(unique_entries):
            if i in merged_indices:
                continue

            best_match_idx = None
            best_similarity = 0.0

            for j, entry_b in enumerate(unique_entries):
                if i == j or j in merged_indices:
                    continue

                similarity = self._jaccard_similarity(
                    entry_a.content, entry_b.content
                )
                if similarity >= similarity_threshold and similarity > best_similarity:
                    best_similarity = similarity
                    best_match_idx = j

            if best_match_idx is not None:
                entry_b = unique_entries[best_match_idx]
                merged_content = self._merge_entry_content(
                    entry_a.content, entry_b.content
                )
                previous_approval = entry_a.approval_status
                previous_lifecycle = entry_a.lifecycle_status
                entry_a.content = merged_content
                entry_a.usage_count += entry_b.usage_count
                entry_a.updated_at = max(
                    entry_a.updated_at, entry_b.updated_at
                )
                entry_a.tags = sorted(
                    list(set(entry_a.tags + entry_b.tags))
                )
                safety = assess_memory_safety(entry_a.content, source="compress")
                if previous_approval != _APPROVAL_REJECTED and previous_lifecycle != "rejected":
                    self._set_entry_safety_and_approval(
                        entry_a,
                        safety,
                        actor="safety_gate",
                        reason="compression merge invalidated approval",
                        lifecycle_status=entry_a.lifecycle_status,
                        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
                    )
                else:
                    entry_a.safety_status = safety.status
                    entry_a.safety_reason = safety.reason
                    entry_a.approval_content_hash = _approval_hash_for_entry(entry_a)
                self._append_approval_audit(
                    scope,
                    entry_a,
                    action="compress_merge",
                    actor="safety_gate",
                    previous_approval=previous_approval,
                    previous_lifecycle=previous_lifecycle,
                    reason="compression merge invalidated approval",
                    safety=safety,
                )
                merged_indices.add(best_match_idx)
                merged_count += 1

            final_entries.append(entry_a)

        self.memories[scope].entries = final_entries
        self.memories[scope]._rebuild_indices()
        self._save_scope(scope)

        return {
            "merged_count": merged_count,
            "removed_count": duplicates_removed,
            "remaining_count": len(final_entries),
        }

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        """Compute Jaccard similarity between two text strings.

        Uses token-based Jaccard similarity: |A ∩ B| / |A ∪ B|

        Args:
            text_a: First text string
            text_b: Second text string

        Returns:
            Similarity score between 0.0 and 1.0
        """
        tokens_a = set(_tokenize(text_a))
        tokens_b = set(_tokenize(text_b))

        if not tokens_a and not tokens_b:
            return 1.0
        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union)

    @staticmethod
    def _merge_entry_content(content_a: str, content_b: str) -> str:
        """Merge two similar content strings.

        Keeps the longer version, appends unique parts from the shorter.

        Args:
            content_a: First content string
            content_b: Second content string

        Returns:
            Merged content string
        """
        if len(content_a) >= len(content_b):
            return content_a
        return content_b

    def detect_conflicts(self, content: str, scope: MemoryScope | None = None, threshold: float = 0.6) -> list[tuple[MemoryEntry, float]]:
        """Detect potential conflicts between new content and existing memories.

        Uses Jaccard similarity on token sets to identify entries that may
        contradict or overlap with the proposed new memory content.

        Args:
            content: New memory content to check for conflicts
            scope: Scope to check (None = all scopes)
            threshold: Similarity threshold for conflict flagging (0.0-1.0)

        Returns:
            List of (entry, similarity) tuples sorted by similarity descending
        """
        new_tokens = set(_tokenize(content))
        if not new_tokens:
            return []

        conflicts: list[tuple[MemoryEntry, float]] = []
        scopes = [scope] if scope else list(MemoryScope)

        for s in scopes:
            if s not in self.memories:
                continue
            for entry in self.memories[s].entries:
                old_tokens = set(entry.get_tokens())
                if not old_tokens:
                    continue
                intersection = new_tokens & old_tokens
                union = new_tokens | old_tokens
                similarity = len(intersection) / len(union) if union else 0.0
                if similarity >= threshold:
                    conflicts.append((entry, similarity))

        conflicts.sort(key=lambda x: x[1], reverse=True)
        return conflicts

    @_coordinated_all_write
    def decay_memories(self, max_age_days: float = 30.0, decay_factor: float = 0.5) -> int:
        """Apply time-based decay to memory usage_count.

        Entries older than max_age_days have their usage_count halved
        (multiplied by decay_factor), reducing their search ranking.
        Returns number of entries decayed.
        """
        now = time.time()
        decayed = 0
        for scope in MemoryScope:
            if scope not in self.memories:
                continue
            for entry in self.memories[scope].entries:
                age_days = (now - entry.updated_at) / 86400.0
                if age_days > max_age_days and entry.usage_count > 0:
                    entry.usage_count = max(0, int(entry.usage_count * decay_factor))
                    decayed += 1
        if decayed:
            for scope in MemoryScope:
                self._save_scope(scope)
        return decayed

    @_coordinated_all_write
    def promote_memories(self) -> dict[str, int]:
        """Promote/demote memories across tiers based on usage and age.

        WORKING → SHORT_TERM → LONG_TERM → ARCHIVAL
        Returns counts per operation.
        """
        now = time.time()
        stats = {"promoted_to_long": 0, "demoted_to_archival": 0, "reactivated": 0}
        for scope in MemoryScope:
            if scope not in self.memories:
                continue
            for entry in self.memories[scope].entries:
                if not entry.is_active:
                    continue
                age_days = (now - entry.updated_at) / 86400.0
                accessed_days = (now - entry.last_accessed) / 86400.0
                if entry.tier == MemoryTier.SHORT_TERM and entry.usage_count >= 5 and age_days > 7:
                    entry.tier = MemoryTier.LONG_TERM
                    entry.tier_reason = "usage"
                    stats["promoted_to_long"] += 1
                    continue
                if entry.tier == MemoryTier.LONG_TERM and accessed_days > 30:
                    previous_approval = entry.approval_status
                    previous_lifecycle = entry.lifecycle_status
                    entry.tier = MemoryTier.ARCHIVAL
                    entry.tier_reason = "age_decay"
                    entry.content = self._summarize_content(entry.content)
                    entry.invalidate_tokens()
                    safety = assess_memory_safety(entry.content, source="curator")
                    entry.safety_status = safety.status
                    entry.safety_reason = safety.reason
                    entry.approval_status = (
                        _APPROVAL_REJECTED
                        if safety.status == _SAFETY_UNSAFE
                        else _APPROVAL_PENDING
                    )
                    entry.approval_reason = "curator summary rewrite requires approval"
                    entry.approval_actor = "curator"
                    entry.approval_decided_at = time.time()
                    entry.approval_content_hash = _approval_hash_for_entry(entry)
                    if safety.status == _SAFETY_UNSAFE:
                        entry.lifecycle_status = "rejected"
                    self._append_approval_audit(
                        scope,
                        entry,
                        action="curator_rewrite",
                        actor="curator",
                        previous_approval=previous_approval,
                        previous_lifecycle=previous_lifecycle,
                        reason=entry.approval_reason,
                        safety=safety,
                    )
                    stats["demoted_to_archival"] += 1
                    continue
                if entry.tier == MemoryTier.ARCHIVAL and accessed_days < 7:
                    entry.tier = MemoryTier.SHORT_TERM
                    entry.tier_reason = "recent_access"
                    stats["reactivated"] += 1
        if any(stats.values()):
            for scope in MemoryScope:
                self._save_scope(scope)
        return stats

    @_coordinated_all_write
    def link_memories(self, similarity_threshold: float = 0.4) -> int:
        """Auto-link related memories by content similarity. Returns link count."""
        links = 0
        for scope in MemoryScope:
            if scope not in self.memories:
                continue
            entries = self.memories[scope].entries
            for i, a in enumerate(entries):
                for j, b in enumerate(entries):
                    if i >= j:
                        continue
                    if b.id in a.related_to:
                        continue
                    if self._jaccard_similarity(a.content, b.content) >= similarity_threshold:
                        a.related_to.append(b.id)
                        b.related_to.append(a.id)
                        a.updated_at = time.time()
                        b.updated_at = time.time()
                        links += 2
        if links:
            for scope in MemoryScope:
                self._save_scope(scope)
        return links

    def get_linked_memories(self, entry_id: str, depth: int = 1) -> list[MemoryEntry]:
        """Get memories linked to entry_id via related_to graph (BFS up to depth)."""
        entry = None
        found_scope = None
        for s in MemoryScope:
            if s in self.memories:
                entry = self.memories[s]._id_index.get(entry_id)
                if entry:
                    found_scope = s
                    break
        if not entry or not entry.related_to or not found_scope:
            return []
        visited = {entry_id}
        frontier = list(entry.related_to)
        results = []
        for _ in range(depth):
            nxt = []
            for rid in frontier:
                if rid in visited:
                    continue
                visited.add(rid)
                linked = self.memories[found_scope]._id_index.get(rid)
                if linked:
                    results.append(linked)
                    nxt.extend(linked.related_to)
            frontier = nxt
            if not frontier:
                break
        return results

    @staticmethod
    def _summarize_content(content: str, max_len: int = 150) -> str:
        if len(content) <= max_len:
            return content
        for sep in [". ", ".\n", "; ", ";\n", "\n"]:
            idx = content.find(sep)
            if 20 < idx < max_len:
                return content[:idx + 1]
        return content[:max_len] + "..."

    def _find_entry_indices(self, scope: MemoryScope, entry_id: str) -> list[int]:
        """Find all indices of entries with a given ID."""
        indices = []
        for idx, entry in enumerate(self.memories[scope].entries):
            if entry.id == entry_id:
                indices.append(idx)
        return indices


# ---------------------------------------------------------------------------
# System prompt integration
# ---------------------------------------------------------------------------

def inject_memory_into_prompt(
    system_prompt: str,
    memory_manager: MemoryManager,
    max_tokens: int = 8000,
    *,
    query: str | None = None,
    current_files: list[str] | None = None,
    active_domains: list[str] | None = None,
    management_mode: bool = False,
) -> str:
    """Compatibility injection facade backed by canonical retrieval.

    Production callers must provide a query. ``management_mode`` preserves an
    explicit human-facing all-active view but does not record injection usage.
    """
    normalized_query = (query or "").strip()
    if not normalized_query:
        if management_mode:
            memory_context = memory_manager.get_relevant_context(max_tokens=max_tokens)
            if not memory_context:
                return system_prompt
            return f"""{system_prompt}

## Project Memory & Context

The following information has been accumulated from previous sessions:

{memory_context}

Use this context to inform your decisions and follow established patterns."""
        return system_prompt

    from minicode.memory_retrieval import (
        CanonicalMemoryRetriever,
        MemoryRetrievalRequest,
        RetrievalSource,
    )

    result = CanonicalMemoryRetriever(memory_manager).retrieve(
        MemoryRetrievalRequest(
            query=normalized_query,
            current_files=tuple(current_files or ()),
            active_domains=tuple(active_domains or ()),
            max_total_tokens=max_tokens,
            source_entrypoint=RetrievalSource.COMPATIBILITY,
        )
    )
    memory_context = result.prompt_text
    
    if not memory_context:
        return system_prompt

    injected_prompt = f"""{system_prompt}

## Project Memory & Context

The following information has been accumulated from previous sessions:

{memory_context}

Use this context to inform your decisions and follow established patterns."""
    memory_manager.record_retrievals_and_injections(
        list(result.selected_ids),
        list(result.rendered_ids),
    )
    return injected_prompt


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def format_memory_list(memory_manager=None, scope: MemoryScope | None = None, category: str | None = None) -> str:
    """Format memory entries for CLI display."""
    if memory_manager is None:
        return "No MemoryManager available."

    # Collect entries from specified scope(s)
    scopes = [scope] if scope else list(MemoryScope)
    all_entries: list[MemoryEntry] = []
    for s in scopes:
        if s in memory_manager.memories:
            entries = memory_manager.memories[s].entries
            if category:
                entries = [e for e in entries if e.category == category]
            all_entries.extend(entries)

    if not all_entries:
        return "No memories found."

    lines = [f"{'=' * 60}"]
    for entry in all_entries[:20]:  # Limit to 20 entries
        scope_tag = f"[{entry.scope.value if hasattr(entry, 'scope') else '?'}]"
        cat_tag = f"[{entry.category}]"
        content_preview = entry.content[:100].replace('\n', ' ')
        lines.append(f"{scope_tag} {cat_tag} {content_preview}")
        if entry.tags:
            lines.append(f"     Tags: {', '.join(entry.tags[:5])}")
        lines.append(f"     Used: {entry.usage_count}x | Updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.updated_at))}")
        lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append(f"Total: {len(all_entries)} entries")
    return "\n".join(lines)
