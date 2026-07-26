"""Deterministic persistent-memory retrieval and prompt-rendering contract."""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable

from minicode.context_manager import estimate_tokens

if TYPE_CHECKING:
    from minicode.memory import MemoryEntry, MemoryManager
    from minicode.memory_injector import MemoryInjectionController


_SPACE_RE = re.compile(r"\s+")
_SEPARATOR_RE = re.compile(r"[_./\\:-]+")
_GENERIC_TERMS = {
    "a",
    "add",
    "an",
    "and",
    "apply",
    "benchmark",
    "build",
    "change",
    "check",
    "code",
    "compute",
    "correct",
    "create",
    "current",
    "design",
    "ensure",
    "exact",
    "feature",
    "file",
    "fix",
    "for",
    "from",
    "general",
    "generator",
    "guidance",
    "handle",
    "implement",
    "implementation",
    "improve",
    "in",
    "make",
    "new",
    "of",
    "on",
    "repair",
    "review",
    "rule",
    "safe",
    "simulate",
    "synthetic",
    "test",
    "the",
    "to",
    "unrelated",
    "update",
    "use",
    "with",
    "修复",
    "新增",
    "更新",
    "使用",
    "验证",
    "改进",
}
_ERROR_TERMS = {
    "deadlock",
    "error",
    "exhaustion",
    "failed",
    "failure",
    "locked",
    "mismatch",
    "timeout",
    "traceback",
    "typeerror",
    "错误",
    "失败",
    "死锁",
    "超时",
}
_TOOL_TERMS = {
    "docker",
    "git",
    "pytest",
    "ruff",
    "sqlite",
}
_CATEGORY_INTENTS: dict[str, set[str]] = {
    "architecture": {"architecture", "contract", "structure"},
    "compatibility": {"backward", "caller", "compatibility", "compatible", "legacy"},
    "configuration": {"config", "configure", "setting", "settings"},
    "database": {"database", "index", "migration", "query", "schema", "transaction"},
    "deployment": {"deploy", "deployment", "docker", "release"},
    "failure-recovery": {"error", "failed", "failure", "recover", "recovery", "retry", "timeout"},
    "performance": {"cache", "congestion", "optimize", "performance", "queue", "slow", "timeout"},
    "security": {"auth", "csrf", "oauth", "password", "security", "signature", "token", "webhook"},
    "testing": {"assert", "fixture", "pytest", "snapshot", "test", "testing"},
}


class RetrievalSource(str, Enum):
    CANONICAL = "canonical_retrieval"
    MANAGER_SEARCH = "manager_global_search"
    MANAGER_CONTEXT = "manager_context_query"
    PIPELINE_READ = "pipeline_read"
    PIPELINE_INJECT = "pipeline_inject"
    INJECTOR = "memory_injector"
    COMPATIBILITY = "compatibility_helper"
    TUI = "tui"
    STDIN = "stdin"
    HEADLESS = "headless"
    AGENT_LOOP = "agent_loop"
    COMPACTOR = "context_compactor"
    FAILURE_RECOVERY = "failure_recovery"
    MANAGEMENT = "management"


def _normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return _SPACE_RE.sub(" ", text).strip()


def _stable_tuple(values: Iterable[object] | None, *, lowercase: bool = False) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        item = _normalize_text(value)
        if lowercase:
            item = item.lower()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class MemoryRetrievalRequest:
    query: str
    current_files: tuple[str, ...] = ()
    active_domains: tuple[str, ...] = ()
    context_usage: float = 0.0
    max_memories: int = 5
    max_total_tokens: int = 1200
    max_tokens_per_memory: int = 200
    min_relevance: float = 0.0
    source_entrypoint: RetrievalSource = RetrievalSource.CANONICAL
    recent_failure: bool = False
    task_outcome_context: str = ""
    allow_queryless: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _normalize_text(self.query))
        object.__setattr__(self, "current_files", _stable_tuple(self.current_files))
        object.__setattr__(self, "active_domains", _stable_tuple(self.active_domains, lowercase=True))
        usage = float(self.context_usage)
        if not math.isfinite(usage):
            raise ValueError("context_usage must be finite")
        object.__setattr__(self, "context_usage", max(0.0, min(1.0, usage)))
        for name in ("max_memories", "max_total_tokens", "max_tokens_per_memory"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        relevance = float(self.min_relevance)
        if not math.isfinite(relevance):
            raise ValueError("min_relevance must be finite")
        object.__setattr__(self, "min_relevance", max(0.0, min(1.0, relevance)))
        try:
            source = (
                self.source_entrypoint
                if isinstance(self.source_entrypoint, RetrievalSource)
                else RetrievalSource(str(self.source_entrypoint))
            )
        except ValueError as exc:
            raise ValueError("source_entrypoint must be a supported RetrievalSource") from exc
        object.__setattr__(self, "source_entrypoint", source)
        object.__setattr__(
            self,
            "task_outcome_context",
            _normalize_text(self.task_outcome_context)[:240],
        )


@dataclass(frozen=True)
class RetrievalScore:
    lexical_score: float = 0.0
    tag_score: float = 0.0
    category_score: float = 0.0
    file_score: float = 0.0
    domain_score: float = 0.0
    scope_score: float = 0.0
    tier_score: float = 0.0
    recency_score: float = 0.0
    usefulness_score: float = 0.0
    final_score: float = 0.0
    matched_terms: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        names = (
            "lexical_score",
            "tag_score",
            "category_score",
            "file_score",
            "domain_score",
            "scope_score",
            "tier_score",
            "recency_score",
            "usefulness_score",
            "final_score",
        )
        for name in names:
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError("RetrievalScore components must be finite")
        object.__setattr__(self, "matched_terms", tuple(self.matched_terms))
        object.__setattr__(self, "reason_codes", tuple(dict.fromkeys(self.reason_codes)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "lexical_score": self.lexical_score,
            "tag_score": self.tag_score,
            "category_score": self.category_score,
            "file_score": self.file_score,
            "domain_score": self.domain_score,
            "scope_score": self.scope_score,
            "tier_score": self.tier_score,
            "recency_score": self.recency_score,
            "usefulness_score": self.usefulness_score,
            "final_score": self.final_score,
            "matched_terms": list(self.matched_terms),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class RetrievedMemory:
    entry_id: str
    scope: str
    category: str
    content: str
    score: RetrievalScore
    rank: int
    token_count: int
    truncated: bool
    source: str
    reason_codes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    authority_signals: tuple[str, ...] = ()
    relations: tuple[tuple[str, str], ...] = ()
    updated_at: float = field(default=0.0, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "scope": self.scope,
            "category": self.category,
            "content": self.content,
            "score": self.score.to_dict(),
            "rank": self.rank,
            "token_count": self.token_count,
            "truncated": self.truncated,
            "source": self.source,
            "reason_codes": list(self.reason_codes),
            "tags": list(self.tags),
            "domains": list(self.domains),
            "authority_signals": list(self.authority_signals),
            "relations": [list(relation) for relation in self.relations],
        }


@dataclass(frozen=True)
class MemoryRetrievalResult:
    candidate_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    rendered_ids: tuple[str, ...] = ()
    suppressed_ids: tuple[str, ...] = ()
    candidates: tuple[RetrievedMemory, ...] = ()
    selected: tuple[RetrievedMemory, ...] = ()
    rendered: tuple[RetrievedMemory, ...] = ()
    prompt_text: str = ""
    no_match: bool = True
    no_match_reason: str = "no_candidates"
    controller_decision: dict[str, Any] = field(default_factory=dict)
    total_tokens: int = 0
    query_hash: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def without_rendered(self, reason: str) -> "MemoryRetrievalResult":
        suppressed = tuple(dict.fromkeys((*self.suppressed_ids, *self.rendered_ids)))
        diagnostics = dict(self.diagnostics)
        diagnostics["render_failure_reason"] = reason
        return replace(
            self,
            rendered_ids=(),
            rendered=(),
            suppressed_ids=suppressed,
            prompt_text="",
            no_match=True,
            no_match_reason=reason,
            total_tokens=0,
            diagnostics=diagnostics,
        )


def _normalized_match_text(value: str) -> str:
    return _SPACE_RE.sub(" ", _SEPARATOR_RE.sub(" ", _normalize_text(value).lower())).strip()


def _stem(term: str) -> str:
    if not term.isascii() or len(term) < 4:
        return term
    if term.endswith("ies") and len(term) > 4:
        return term[:-3] + "y"
    if term.endswith("ing") and len(term) > 5:
        base = term[:-3]
        return base + "e" if base.endswith(("c", "v")) else base
    if term.endswith("ed") and len(term) > 4:
        base = term[:-2]
        return base + "e" if base.endswith(("c", "u", "v")) else base
    if term.endswith("es") and len(term) > 4:
        return term[:-2]
    if term.endswith("s") and len(term) > 3:
        return term[:-1]
    return term


def _token_set(text: str, active_domains: tuple[str, ...] = ()) -> set[str]:
    from minicode.memory import _expand_query_terms, _tokenize

    tokens = _tokenize(_normalized_match_text(text))
    expanded = _expand_query_terms(tokens, active_domains=list(active_domains) or None)
    return {_stem(token.lower()) for token in expanded if token.strip()}


def _informative_terms(query: str, active_domains: tuple[str, ...]) -> tuple[str, ...]:
    from minicode.memory import _tokenize

    original = _tokenize(_normalized_match_text(query))
    informative: list[str] = []
    for token in original:
        normalized = _stem(token.lower())
        if normalized in _GENERIC_TERMS:
            continue
        if normalized.isascii() and len(normalized) < 2:
            continue
        if not normalized.isascii() and len(normalized) < 2:
            continue
        if normalized not in informative:
            informative.append(normalized)
    return tuple(informative)


def _category_score(category: str, query_terms: set[str]) -> float:
    normalized = _normalized_match_text(category)
    intent_terms = _CATEGORY_INTENTS.get(normalized, {normalized})
    return 1.0 if query_terms & {_stem(term) for term in intent_terms} else 0.0


def _entry_source(entry: "MemoryEntry") -> str:
    return entry.source or f"{entry.scope.value}_memory"


class DeterministicRetrievalGate:
    """Accept only candidates with positive, explainable task evidence."""

    def evaluate(
        self,
        *,
        informative_terms: tuple[str, ...],
        matched_terms: tuple[str, ...],
        phrase_match: bool,
        exact_tag_match: bool,
        file_match: bool,
        domain_match: bool,
        category_match: bool,
        project_tag_object_match: bool,
        user_context_object_match: bool,
        recent_failure: bool,
    ) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        match_count = len(matched_terms)
        coverage = match_count / len(informative_terms) if informative_terms else 0.0
        if phrase_match:
            reasons.append("phrase_match")
        if exact_tag_match:
            reasons.append("exact_tag_match")
        if file_match:
            reasons.append("file_match")
        if match_count >= 2:
            reasons.append("multiple_informative_terms")
        if match_count >= 1 and coverage >= 0.60:
            reasons.append("high_token_coverage")
        if domain_match and match_count >= 1:
            reasons.append("domain_with_lexical_evidence")
        if category_match and match_count >= 1:
            reasons.append("category_intent_with_object")
        if project_tag_object_match:
            reasons.append("project_exact_tag_object")
        if user_context_object_match:
            reasons.append("explicit_user_context_object")
        if recent_failure and set(matched_terms) & (_ERROR_TERMS | _TOOL_TERMS):
            reasons.append("failure_signature_match")
        return bool(reasons), tuple(reasons or ["insufficient_relevance_evidence"])


class CanonicalMemoryRetriever:
    """The single deterministic implementation for production memory retrieval."""

    _HEADER = "## Relevant Context from Memory\n\n"
    _FOOTER = "\n\nUse the above context to inform your decisions."

    def __init__(
        self,
        memory_manager: "MemoryManager",
        *,
        controller: "MemoryInjectionController | None" = None,
        gate: DeterministicRetrievalGate | None = None,
        consolidator: Any | None = None,
    ) -> None:
        self._memory = memory_manager
        self._controller = controller
        self._gate = gate or DeterministicRetrievalGate()
        if consolidator is None:
            from minicode.memory_candidate_consolidation import CandidateConsolidator

            consolidator = CandidateConsolidator()
        self._consolidator = consolidator

    @staticmethod
    def _query_hash(query: str) -> str:
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16] if query else ""

    def _domains(self, request: MemoryRetrievalRequest) -> tuple[tuple[str, ...], str]:
        if request.active_domains:
            return request.active_domains, "explicit"
        try:
            from minicode.domain_classifier import get_active_domain_values

            derived = tuple(
                domain
                for domain in get_active_domain_values(
                    current_files=list(request.current_files) or None,
                    intent_text=request.query,
                )
                if domain != "general"
            )
        except Exception:
            derived = ()
        if not derived:
            return (), "unavailable"
        if request.current_files:
            return derived, "file_derived"
        return derived, "intent_derived"

    def _decision(self, request: MemoryRetrievalRequest, domains: tuple[str, ...]) -> dict[str, Any]:
        if self._controller is None:
            from minicode.memory_injector import MemoryInjectionController

            self._controller = MemoryInjectionController()
        from minicode.memory_injector import MemoryInjectionSignal

        decision = self._controller.decide(
            MemoryInjectionSignal(
                context_usage=request.context_usage,
                retrieval_quality=0.5,
                recent_failure=request.recent_failure,
                active_domains=list(domains),
            ),
            base_max_memories=request.max_memories,
            base_min_relevance=request.min_relevance,
            base_max_tokens=request.max_tokens_per_memory,
        )
        result = decision.to_dict()
        result["max_memories"] = min(request.max_memories, decision.max_memories)
        result["max_tokens_per_memory"] = min(
            request.max_tokens_per_memory,
            decision.max_tokens_per_memory,
        )
        return result

    def _active_entries(self) -> list["MemoryEntry"]:
        entries = [
            entry
            for memory_file in self._memory.memories.values()
            for entry in memory_file.entries
            if entry.is_active
        ]
        return sorted(entries, key=lambda entry: (entry.scope.value, entry.id))

    def _manager_scores(
        self,
        query: str,
        domains: tuple[str, ...],
        count: int,
    ) -> dict[str, float]:
        if not query or count <= 0:
            return {}
        entries = self._memory.search(
            query,
            scope=None,
            limit=count,
            min_relevance=0.0,
            active_domains=list(domains) or None,
            record_usage=False,
        )
        return {
            entry.id: max(0.0, float(getattr(entry, "_last_relevance", 0.0)))
            for entry in entries
        }

    def _score_entry(
        self,
        entry: "MemoryEntry",
        request: MemoryRetrievalRequest,
        domains: tuple[str, ...],
        informative: tuple[str, ...],
        manager_score: float,
    ) -> tuple[RetrievedMemory, bool, tuple[str, ...]]:
        from minicode.memory import MemoryScope, MemoryTier
        from minicode.memory_candidate_consolidation import extract_candidate_signals

        query_terms = set(informative)
        entry_text = " ".join(
            (
                entry.content,
                entry.category,
                " ".join(entry.tags),
                " ".join(entry.domains),
            )
        )
        entry_terms = _token_set(entry_text, domains)
        matched = tuple(sorted(query_terms & entry_terms))
        coverage = len(matched) / len(informative) if informative else 0.0
        normalized_query_phrase = " ".join(informative)
        normalized_entry = _normalized_match_text(entry_text)
        phrase_match = (
            len(informative) >= 2
            and normalized_query_phrase in normalized_entry
        )
        normalized_tags = {_normalized_match_text(tag) for tag in entry.tags}
        exact_tag_match = bool(normalized_query_phrase and normalized_query_phrase in normalized_tags)
        tag_terms = _token_set(" ".join(entry.tags), domains)

        normalized_files = [
            (_normalized_match_text(path), _normalized_match_text(os.path.basename(path)))
            for path in request.current_files
        ]
        file_match = any(
            (full_path and full_path in normalized_entry)
            or (basename and basename in normalized_entry)
            for full_path, basename in normalized_files
        )
        active_domain_set = set(domains)
        entry_domain_set = {domain.lower() for domain in entry.domains}
        domain_union = active_domain_set | entry_domain_set
        domain_score = (
            len(active_domain_set & entry_domain_set) / len(domain_union)
            if domain_union and active_domain_set
            else 0.0
        )
        category_score = _category_score(entry.category, query_terms)
        category_match = category_score > 0
        accepted, gate_reasons = self._gate.evaluate(
            informative_terms=informative,
            matched_terms=matched,
            phrase_match=phrase_match,
            exact_tag_match=exact_tag_match,
            file_match=file_match,
            domain_match=domain_score > 0,
            category_match=category_match,
            project_tag_object_match=(
                entry.scope.value == "project"
                and len(matched) == 1
                and bool(set(matched) & tag_terms)
            ),
            user_context_object_match=(
                entry.scope.value == "user"
                and _normalized_match_text(entry.category)
                in {"testing", "preference", "convention"}
                and len(matched) == 1
                and bool(set(matched) & tag_terms)
            ),
            recent_failure=request.recent_failure,
        )

        manager_lexical = manager_score / (manager_score + 1.0) if manager_score > 0 else 0.0
        lexical_score = max(coverage, manager_lexical, 1.0 if phrase_match else 0.0)
        tag_score = len(query_terms & tag_terms) / len(query_terms) if query_terms else 0.0
        file_score = 1.0 if file_match else 0.0
        scope_score = {
            MemoryScope.PROJECT: 1.0,
            MemoryScope.LOCAL: 0.7,
            MemoryScope.USER: 0.5,
        }.get(entry.scope, 0.0)
        tier_score = {
            MemoryTier.WORKING: 0.8,
            MemoryTier.SHORT_TERM: 0.7,
            MemoryTier.LONG_TERM: 1.0,
        }.get(entry.tier, 0.0)
        age_days = max(0.0, (time.time() - entry.updated_at) / 86400.0)
        recency_score = 1.0 / (1.0 + age_days / 30.0)
        usefulness_score = max(-1.0, min(1.0, float(entry.usefulness_score)))
        final_score = max(
            0.0,
            lexical_score * 0.72
            + tag_score * 0.10
            + category_score * 0.035
            + file_score * 0.08
            + domain_score * 0.035
            + scope_score * 0.02
            + tier_score * 0.005
            + recency_score * 0.005
            + usefulness_score * 0.005,
        )
        reasons = list(gate_reasons)
        if domain_score > 0:
            reasons.append("domain_match")
        if category_match:
            reasons.append("category_intent_match")
        score = RetrievalScore(
            lexical_score=lexical_score,
            tag_score=tag_score,
            category_score=category_score,
            file_score=file_score,
            domain_score=domain_score,
            scope_score=scope_score,
            tier_score=tier_score,
            recency_score=recency_score,
            usefulness_score=usefulness_score,
            final_score=final_score,
            matched_terms=matched,
            reason_codes=tuple(reasons),
        )
        candidate_signals = extract_candidate_signals(entry)
        memory = RetrievedMemory(
            entry_id=entry.id,
            scope=entry.scope.value,
            category=entry.category,
            content=entry.content,
            score=score,
            rank=0,
            token_count=estimate_tokens(entry.content),
            truncated=False,
            source=_entry_source(entry),
            reason_codes=score.reason_codes,
            tags=tuple(entry.tags),
            domains=tuple(entry.domains),
            authority_signals=candidate_signals.authority_signals,
            relations=candidate_signals.relations,
            updated_at=entry.updated_at,
        )
        return memory, accepted and final_score >= request.min_relevance, tuple(reasons)

    @staticmethod
    def _truncate(content: str, max_tokens: int) -> tuple[str, bool]:
        if max_tokens <= 0:
            return "", bool(content)
        if estimate_tokens(content) <= max_tokens:
            return content, False
        suffix = "..."
        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = content[:middle].rstrip() + suffix
            if estimate_tokens(candidate) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        return content[:low].rstrip() + suffix, True

    def _render(
        self,
        selected: tuple[RetrievedMemory, ...],
        request: MemoryRetrievalRequest,
        decision: dict[str, Any],
    ) -> tuple[tuple[RetrievedMemory, ...], str, tuple[str, ...]]:
        max_count = min(request.max_memories, int(decision.get("max_memories", 0)))
        max_per_memory = min(
            request.max_tokens_per_memory,
            int(decision.get("max_tokens_per_memory", 0)),
        )
        if max_count <= 0 or request.max_total_tokens <= 0 or max_per_memory <= 0:
            return (), "", tuple(memory.entry_id for memory in selected)
        if estimate_tokens(self._HEADER + self._FOOTER) > request.max_total_tokens:
            return (), "", tuple(memory.entry_id for memory in selected)

        rendered: list[RetrievedMemory] = []
        lines: list[str] = []
        skipped: list[str] = []
        for memory in selected:
            if len(rendered) >= max_count:
                skipped.append(memory.entry_id)
                continue
            content, truncated = self._truncate(memory.content, max_per_memory)
            if not content:
                skipped.append(memory.entry_id)
                continue
            line = f"{len(rendered) + 1}. [{memory.category}] {content}"
            candidate_prompt = self._HEADER + "\n".join((*lines, line)) + self._FOOTER
            if estimate_tokens(candidate_prompt) > request.max_total_tokens:
                skipped.append(memory.entry_id)
                continue
            lines.append(line)
            rendered.append(
                replace(
                    memory,
                    content=content,
                    rank=len(rendered) + 1,
                    token_count=estimate_tokens(content),
                    truncated=truncated,
                    reason_codes=tuple(
                        dict.fromkeys(
                            (
                                *memory.reason_codes,
                                *(("truncated_to_per_memory_budget",) if truncated else ()),
                            )
                        )
                    ),
                )
            )
        prompt = self._HEADER + "\n".join(lines) + self._FOOTER if lines else ""
        return tuple(rendered), prompt, tuple(skipped)

    def retrieve(self, request: MemoryRetrievalRequest) -> MemoryRetrievalResult:
        query_hash = self._query_hash(request.query)
        if not request.query and not request.allow_queryless:
            return MemoryRetrievalResult(
                no_match=True,
                no_match_reason="queryless_production_request",
                query_hash=query_hash,
                controller_decision={"mode": "none", "max_memories": 0},
                diagnostics={"domain_source": "unavailable", "reason_codes": ["queryless_fail_closed"]},
            )

        domains, domain_source = self._domains(request)
        informative = _informative_terms(request.query, domains)
        if not informative and not request.allow_queryless:
            return MemoryRetrievalResult(
                no_match=True,
                no_match_reason="query_has_no_informative_terms",
                query_hash=query_hash,
                controller_decision={"mode": "none", "max_memories": 0},
                diagnostics={"domain_source": domain_source, "reason_codes": ["no_informative_terms"]},
            )

        active_entries = self._active_entries()
        manager_scores = self._manager_scores(request.query, domains, len(active_entries))
        candidates_with_gate = [
            self._score_entry(
                entry,
                request,
                domains,
                informative,
                manager_scores.get(entry.id, 0.0),
            )
            for entry in active_entries
        ]
        candidates = [item[0] for item in candidates_with_gate]
        candidates.sort(
            key=lambda memory: (
                -memory.score.final_score,
                -memory.updated_at,
                memory.scope,
                memory.entry_id,
            )
        )
        ranked_candidates = tuple(replace(memory, rank=index) for index, memory in enumerate(candidates, 1))
        accepted_ids = {memory.entry_id for memory, accepted, _ in candidates_with_gate if accepted}
        selected_pre_dedupe = [memory for memory in ranked_candidates if memory.entry_id in accepted_ids]

        selected_after_gate: list[RetrievedMemory] = []
        seen_content: set[str] = set()
        duplicate_ids: list[str] = []
        for memory in selected_pre_dedupe:
            content_key = _normalized_match_text(memory.content)
            if content_key in seen_content:
                duplicate_ids.append(memory.entry_id)
                continue
            seen_content.add(content_key)
            selected_after_gate.append(replace(memory, rank=len(selected_after_gate) + 1))

        consolidation = self._consolidator.consolidate(
            tuple(selected_after_gate),
            request,
        )
        selected = [
            replace(memory, rank=index)
            for index, memory in enumerate(consolidation.retained, 1)
        ]

        decision = self._decision(request, domains)
        rendered, prompt, budget_skipped = self._render(tuple(selected), request, decision)
        rendered_ids = tuple(memory.entry_id for memory in rendered)
        selected_ids = tuple(memory.entry_id for memory in selected)
        gate_suppressed = [
            memory.entry_id for memory in ranked_candidates if memory.entry_id not in accepted_ids
        ]
        consolidation_ids = tuple(item.entry_id for item in consolidation.suppressed)
        suppressed_ids = tuple(
            dict.fromkeys(
                (*gate_suppressed, *duplicate_ids, *consolidation_ids, *budget_skipped)
            )
        )
        suppression_reasons = {
            memory.entry_id: list(memory.reason_codes)
            for memory in ranked_candidates
            if memory.entry_id in suppressed_ids
        }
        if rendered:
            no_match = False
            no_match_reason = ""
        elif decision.get("mode") == "none":
            no_match = True
            no_match_reason = "controller_disabled"
        elif selected:
            no_match = True
            no_match_reason = "budget_exhausted"
        elif any(
            item.reason.value == "unresolved_conflict"
            for item in consolidation.suppressed
        ):
            no_match = True
            no_match_reason = "unresolved_conflict_fail_closed"
        elif selected_after_gate:
            no_match = True
            no_match_reason = "candidate_consolidation_suppressed_all"
        elif active_entries:
            no_match = True
            no_match_reason = "relevance_gate_rejected_all"
        else:
            no_match = True
            no_match_reason = "no_active_memories"
        return MemoryRetrievalResult(
            candidate_ids=tuple(memory.entry_id for memory in ranked_candidates),
            selected_ids=selected_ids,
            rendered_ids=rendered_ids,
            suppressed_ids=suppressed_ids,
            candidates=ranked_candidates,
            selected=tuple(selected),
            rendered=rendered,
            prompt_text=prompt,
            no_match=no_match,
            no_match_reason=no_match_reason,
            controller_decision=decision,
            total_tokens=estimate_tokens(prompt),
            query_hash=query_hash,
            diagnostics={
                "domain_source": domain_source,
                "active_domains": list(domains),
                "informative_terms": list(informative),
                "candidate_count": len(ranked_candidates),
                "post_gate_count": len(selected_after_gate),
                "post_gate_ids": [memory.entry_id for memory in selected_after_gate],
                "post_consolidation_ids": list(selected_ids),
                "selected_count": len(selected),
                "rendered_count": len(rendered),
                "suppressed_reason_codes": suppression_reasons,
                "consolidation": consolidation.diagnostics,
                "consolidation_suppressions": [
                    item.to_dict() for item in consolidation.suppressed
                ],
                "reranker_reason": "reranker_not_part_of_phase2a",
            },
        )
