"""Deterministic lexical/dense fusion for the offline Phase 3B prototype."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*|[\u3400-\u4dbf\u4e00-\u9fff]+", re.I)


def lexical_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(normalized):
        if token.isascii():
            tokens.append(token)
        elif len(token) == 1:
            tokens.append(token)
        else:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


class BM25Index:
    def __init__(self, documents: dict[str, str], *, k1: float = 1.2, b: float = 0.75) -> None:
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("invalid BM25 parameters")
        self.k1 = float(k1)
        self.b = float(b)
        self._terms = {entry_id: Counter(lexical_tokens(text)) for entry_id, text in documents.items()}
        self._lengths = {entry_id: sum(terms.values()) for entry_id, terms in self._terms.items()}
        self._average_length = (
            sum(self._lengths.values()) / len(self._lengths) if self._lengths else 0.0
        )
        self._document_frequency: Counter[str] = Counter()
        for terms in self._terms.values():
            self._document_frequency.update(terms.keys())

    def search(
        self, query: str, *, limit: int, allowed_ids: set[str] | None = None
    ) -> list[tuple[str, float]]:
        if not query.strip() or limit <= 0:
            return []
        query_terms = Counter(lexical_tokens(query))
        count = len(self._terms)
        scores: list[tuple[str, float]] = []
        for entry_id, frequencies in self._terms.items():
            if allowed_ids is not None and entry_id not in allowed_ids:
                continue
            score = 0.0
            length = self._lengths[entry_id]
            for term, query_frequency in query_terms.items():
                tf = frequencies.get(term, 0)
                if not tf:
                    continue
                df = self._document_frequency[term]
                inverse = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
                denominator = tf + self.k1 * (
                    1.0 - self.b + self.b * length / max(self._average_length, 1.0)
                )
                score += inverse * tf * (self.k1 + 1.0) / denominator * query_frequency
            scores.append((entry_id, score))
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:limit]


@dataclass(frozen=True)
class HybridCandidate:
    entry_id: str
    lexical_score: float
    dense_score: float
    fused_score: float
    lexical_rank: int | None
    dense_rank: int | None
    rank: int
    reason_codes: tuple[str, ...]


def _rank_map(items: Sequence[tuple[str, float]]) -> dict[str, int]:
    return {entry_id: rank for rank, (entry_id, _) in enumerate(items, 1)}


def _score_map(items: Sequence[tuple[str, float]]) -> dict[str, float]:
    return dict(items)


def _normalize_scores(items: Sequence[tuple[str, float]]) -> dict[str, float]:
    if not items:
        return {}
    values = [score for _, score in items]
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return {entry_id: (1.0 if high > 0 else 0.0) for entry_id, _ in items}
    return {entry_id: (score - low) / (high - low) for entry_id, score in items}


def fuse_candidates(
    lexical: Sequence[tuple[str, float]],
    dense: Sequence[tuple[str, float]],
    *,
    method: str,
    limit: int,
    rrf_k: int = 60,
    lexical_weight: float = 0.35,
) -> tuple[HybridCandidate, ...]:
    if limit <= 0:
        return ()
    if method not in {"dense", "union", "rrf", "weighted"}:
        raise ValueError("unsupported fusion method")
    if rrf_k <= 0 or not 0.0 <= lexical_weight <= 1.0:
        raise ValueError("invalid fusion configuration")
    lexical_rank = _rank_map(lexical)
    dense_rank = _rank_map(dense)
    dense_raw = _score_map(dense)
    lexical_normalized = _normalize_scores(lexical)
    dense_normalized = _normalize_scores(dense)
    ids = set(dense_rank) if method == "dense" else set(lexical_rank) | set(dense_rank)
    rows: list[HybridCandidate] = []
    for entry_id in ids:
        if method == "dense":
            score = dense_raw[entry_id]
        elif method == "union":
            score = 1.0 / min(lexical_rank.get(entry_id, 10**9), dense_rank.get(entry_id, 10**9))
        elif method == "rrf":
            score = (1.0 / (rrf_k + lexical_rank[entry_id]) if entry_id in lexical_rank else 0.0) + (
                1.0 / (rrf_k + dense_rank[entry_id]) if entry_id in dense_rank else 0.0
            )
        else:
            score = (
                lexical_weight * lexical_normalized.get(entry_id, 0.0)
                + (1.0 - lexical_weight) * dense_normalized.get(entry_id, 0.0)
            )
        reasons = tuple(
            reason
            for reason, present in (
                ("lexical_candidate", entry_id in lexical_rank),
                ("dense_candidate", entry_id in dense_rank),
                (f"fusion_{method}", True),
            )
            if present
        )
        rows.append(
            HybridCandidate(
                entry_id=entry_id,
                lexical_score=lexical_normalized.get(entry_id, 0.0),
                dense_score=dense_raw.get(entry_id, -1.0),
                fused_score=score,
                lexical_rank=lexical_rank.get(entry_id),
                dense_rank=dense_rank.get(entry_id),
                rank=0,
                reason_codes=reasons,
            )
        )
    rows.sort(key=lambda item: (-item.fused_score, item.entry_id))
    return tuple(replace(item, rank=rank) for rank, item in enumerate(rows[:limit], 1))


def consolidate_candidates(
    candidates: Sequence[HybridCandidate],
    entries_by_id: dict[str, dict[str, Any]],
    *,
    query: str,
    current_files: Sequence[str],
    active_domains: Sequence[str],
) -> tuple[tuple[HybridCandidate, ...], tuple[dict[str, Any], ...]]:
    from minicode.context_manager import estimate_tokens
    from minicode.memory import MemoryEntry
    from minicode.memory_candidate_consolidation import (
        CandidateConsolidator,
        extract_candidate_signals,
    )
    from minicode.memory_retrieval import (
        MemoryRetrievalRequest,
        RetrievalScore,
        RetrievedMemory,
    )

    retrieved = []
    pre_suppressions: list[dict[str, Any]] = []
    seen_content: dict[str, str] = {}
    deduplicated: list[HybridCandidate] = []
    for item in candidates:
        normalized_content = " ".join(
            unicodedata.normalize("NFKC", entries_by_id[item.entry_id]["content"])
            .casefold()
            .split()
        )
        if normalized_content in seen_content:
            pre_suppressions.append(
                {
                    "entry_id": item.entry_id,
                    "reason": "exact_content_duplicate",
                    "dominating_candidate_id": seen_content[normalized_content],
                    "chain_key": "",
                    "reason_codes": ["canonical_pre_consolidation_deduplication"],
                }
            )
            continue
        seen_content[normalized_content] = item.entry_id
        deduplicated.append(item)
    for item in deduplicated:
        entry = entries_by_id[item.entry_id]
        memory_entry = MemoryEntry.from_dict(entry)
        signals = extract_candidate_signals(memory_entry)
        score = RetrievalScore(
            lexical_score=item.lexical_score,
            domain_score=1.0 if set(entry.get("domains", [])) & set(active_domains) else 0.0,
            file_score=1.0 if any(path in entry.get("content", "") for path in current_files) else 0.0,
            final_score=max(0.0, item.fused_score),
            reason_codes=item.reason_codes,
        )
        retrieved.append(
            RetrievedMemory(
                entry_id=item.entry_id,
                scope=entry["scope"],
                category=entry["category"],
                content=entry["content"],
                score=score,
                rank=item.rank,
                token_count=estimate_tokens(entry["content"]),
                truncated=False,
                source=entry.get("source", "phase3b_experiment"),
                reason_codes=item.reason_codes,
                tags=tuple(entry.get("tags", [])),
                domains=tuple(entry.get("domains", [])),
                authority_signals=signals.authority_signals,
                relations=signals.relations,
                updated_at=float(entry.get("updated_at", 0.0)),
            )
        )
    request = MemoryRetrievalRequest(
        query=query,
        current_files=tuple(current_files),
        active_domains=tuple(active_domains),
        max_memories=20,
        max_total_tokens=8000,
        max_tokens_per_memory=400,
    )
    result = CandidateConsolidator().consolidate(tuple(retrieved), request)
    retained_ids = set(result.retained_ids)
    retained = tuple(item for item in candidates if item.entry_id in retained_ids)
    suppressions = tuple((*pre_suppressions, *(item.to_dict() for item in result.suppressed)))
    return retained, suppressions


def simulate_controller_and_budget(
    candidates: Iterable[HybridCandidate],
    entries_by_id: dict[str, dict[str, Any]],
    *,
    context_usage: float,
    max_memories: int = 5,
    max_total_tokens: int = 1200,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    from minicode.context_manager import estimate_tokens

    if context_usage >= 0.9:
        ids = tuple(item.entry_id for item in candidates)
        return (), ids, "none"
    rendered: list[str] = []
    skipped: list[str] = []
    tokens = 0
    for item in candidates:
        cost = estimate_tokens(entries_by_id[item.entry_id]["content"])
        if len(rendered) >= max_memories or tokens + cost > max_total_tokens:
            skipped.append(item.entry_id)
            continue
        rendered.append(item.entry_id)
        tokens += cost
    return tuple(rendered), tuple(skipped), "standard"
