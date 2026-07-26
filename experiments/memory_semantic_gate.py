"""Explainable, frozen semantic Gate for the Retrieval Phase 3B prototype."""

from __future__ import annotations

import math
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from experiments.memory_embedding_index import eligibility_reason


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*|[\u3400-\u9fff]", re.I)


@dataclass(frozen=True)
class SemanticGateConfig:
    dense_threshold: float
    lexical_override_threshold: float
    lexical_dense_floor: float
    minimum_top1_margin: float
    structured_bonus: float
    max_accept: int = 1
    max_rank: int = 20
    minimum_query_terms: int = 2

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.dense_threshold <= 1.0:
            raise ValueError("dense_threshold must be in [0, 1]")
        if not 0.0 <= self.lexical_override_threshold <= 1.0:
            raise ValueError("lexical_override_threshold must be in [0, 1]")
        if not 0.0 <= self.lexical_dense_floor <= 1.0:
            raise ValueError("lexical_dense_floor must be in [0, 1]")
        if self.minimum_top1_margin < 0 or self.structured_bonus < 0:
            raise ValueError("margin and structured bonus cannot be negative")
        if self.max_accept <= 0 or self.max_rank <= 0 or self.minimum_query_terms <= 0:
            raise ValueError("integer Gate limits must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticGateDecision:
    entry_id: str
    accepted: bool
    required_dense_score: float
    reason_codes: tuple[str, ...]


def _query_term_count(query: str) -> int:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    return len({item for item in _WORD_RE.findall(normalized) if item.strip()})


def _structured_matches(
    entry: dict[str, Any], current_files: Sequence[str], active_domains: Sequence[str]
) -> tuple[bool, bool, bool]:
    content = unicodedata.normalize("NFKC", str(entry.get("content", ""))).casefold()
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    raw_paths: list[str] = []
    for key in ("files", "file_paths", "paths"):
        value = metadata.get(key, [])
        if isinstance(value, list):
            raw_paths.extend(str(item) for item in value if isinstance(item, str))
    entry_paths = {unicodedata.normalize("NFKC", item).casefold() for item in raw_paths}
    exact_file = any(
        (normalized := unicodedata.normalize("NFKC", path).casefold()) in entry_paths
        or normalized in content
        for path in current_files
    )
    basename_conflict = bool(current_files and entry_paths) and not exact_file and any(
        os.path.basename(path).casefold() in {os.path.basename(item).casefold() for item in entry_paths}
        for path in current_files
    )
    domains = {str(item).casefold() for item in entry.get("domains", []) if isinstance(item, str)}
    domain_match = bool(domains & {str(item).casefold() for item in active_domains})
    return exact_file, domain_match, basename_conflict


class SemanticRelevanceGate:
    def __init__(self, config: SemanticGateConfig) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        query: str,
        candidates: Sequence[Any],
        entries_by_id: dict[str, dict[str, Any]],
        current_files: Sequence[str] = (),
        active_domains: Sequence[str] = (),
    ) -> tuple[SemanticGateDecision, ...]:
        if not query.strip() or _query_term_count(query) < self.config.minimum_query_terms:
            return tuple(
                SemanticGateDecision(
                    item.entry_id,
                    False,
                    self.config.dense_threshold,
                    ("query_insufficient",),
                )
                for item in candidates
            )
        dense_order = sorted(
            (float(item.dense_score) for item in candidates), reverse=True
        )
        top_margin = dense_order[0] - dense_order[1] if len(dense_order) > 1 else 1.0
        decisions: list[SemanticGateDecision] = []
        accepted = 0
        for item in candidates:
            entry = entries_by_id.get(item.entry_id, {})
            reasons: list[str] = []
            eligibility = eligibility_reason(entry)
            if eligibility != "eligible":
                decisions.append(
                    SemanticGateDecision(item.entry_id, False, 1.0, (eligibility,))
                )
                continue
            if item.rank > self.config.max_rank:
                decisions.append(
                    SemanticGateDecision(item.entry_id, False, 1.0, ("rank_limit",))
                )
                continue
            exact_file, domain_match, basename_conflict = _structured_matches(
                entry, current_files, active_domains
            )
            if basename_conflict:
                decisions.append(
                    SemanticGateDecision(item.entry_id, False, 1.0, ("basename_path_conflict",))
                )
                continue
            required = max(
                0.0,
                self.config.dense_threshold
                - (self.config.structured_bonus if exact_file or domain_match else 0.0),
            )
            dense_accept = item.dense_score >= required
            lexical_override = (
                item.lexical_score >= self.config.lexical_override_threshold
                and item.dense_score >= self.config.lexical_dense_floor
            )
            if dense_accept:
                reasons.append("dense_threshold")
            if lexical_override:
                reasons.append("lexical_override")
            if exact_file:
                reasons.append("exact_file_match")
            if domain_match:
                reasons.append("domain_match")
            if item.rank == 1 and top_margin >= self.config.minimum_top1_margin:
                reasons.append("top1_margin")
            margin_ok = item.rank != 1 or top_margin >= self.config.minimum_top1_margin
            allowed = (
                accepted < self.config.max_accept
                and (dense_accept or lexical_override)
                and margin_ok
            )
            if allowed:
                accepted += 1
            else:
                if not (dense_accept or lexical_override):
                    reasons.append("below_relevance_threshold")
                if not margin_ok:
                    reasons.append("insufficient_top1_margin")
                if accepted >= self.config.max_accept:
                    reasons.append("max_accept_reached")
            decisions.append(
                SemanticGateDecision(
                    item.entry_id,
                    allowed,
                    required,
                    tuple(dict.fromkeys(reasons)),
                )
            )
        return tuple(decisions)
