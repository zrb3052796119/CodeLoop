"""Deterministic consolidation for post-gate persistent-memory candidates."""

from __future__ import annotations

import hashlib
import itertools
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from minicode.memory_retrieval import MemoryRetrievalRequest, RetrievedMemory


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*|[\u3400-\u9fff]+", re.I)
_FILE_RE = re.compile(
    r"(?:^|[\s`'\"(])([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+|"
    r"[a-zA-Z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|java|go|rs|rb|php|sql|"
    r"json|ya?ml|toml|ini|cfg|md|sh|css|html))(?=$|[\s`'\"),:;])",
    re.I,
)
_GENERIC_CHAIN_TERMS = {
    "api",
    "apply",
    "build",
    "change",
    "code",
    "current",
    "error",
    "failure",
    "file",
    "fix",
    "general",
    "implement",
    "memory",
    "project",
    "request",
    "review",
    "rule",
    "setting",
    "system",
    "test",
    "update",
    "use",
    "修复",
    "更新",
    "使用",
    "验证",
}
_NON_OBJECT_TERMS = _GENERIC_CHAIN_TERMS | {
    "duplicate",
    "example",
    "examples",
    "format",
    "formatting",
    "heading",
    "headings",
    "label",
    "labels",
    "page",
}
_NOISE_CATEGORIES = {
    "convention",
    "documentation",
    "frontend",
    "preference",
    "style",
}
_USER_CONSTRAINT_CATEGORIES = {
    "convention",
    "preference",
    "style",
    "testing",
}
_DOCUMENTATION_RE = re.compile(
    r"\b(background notes?|buttons?|examples?|glossary|headings?|hostnames?|"
    r"labels?|page|screenshots?|sentence case|tables?|title case|unrelated)\b",
    re.I,
)
_CONSTRAINT_RE = re.compile(
    r"\b(always|explicit|fixed|forbidden|includes?|log|must|never|prefer|requires?|"
    r"should|show|small|use|uses)\b|(?:必须|应当|应该|禁止|偏好|使用)",
    re.I,
)
_VERIFICATION_RE = re.compile(
    r"\b(assert|check|passed|regression|smoke test|test|tests|verify|verified|"
    r"verification)\b|(?:验证|测试|通过)",
    re.I,
)
_VERIFIED_AUTHORITY_RE = re.compile(
    r"\b(confirmed|passed|validated|verified)\b|(?:已经验证|已验证|验证通过|测试通过)",
    re.I,
)
_UNVERIFIED_RE = re.compile(
    r"\b(may|might|not verified|no verification|possible|unverified)\b|"
    r"(?:可能|未经验证|未验证)",
    re.I,
)
_CONFLICT_RISK_RE = re.compile(
    r"\b(all failures?|always|disable|every|forever|infinite|never|"
    r"request order|skip|without delay)\b|(?:永久|无限|全部|跳过|禁用)",
    re.I,
)
_NUMBER_WORD = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand"
)
_QUANTITY_RE = re.compile(
    rf"\b(?:\d+(?:\.\d+)?|(?:{_NUMBER_WORD})(?:-(?:{_NUMBER_WORD}))?)\s*"
    r"(?:ms|milliseconds?|seconds?|minutes?|hours?|days?|times?|retries?)\b",
    re.I,
)
_RELATION_KEYS = {
    "canonical": "canonical",
    "canonical_id": "canonical",
    "conflicts": "conflicts_with",
    "conflicts_with": "conflicts_with",
    "related": "related_to",
    "related_to": "related_to",
    "superseded_by": "superseded_by",
    "supersedes": "supersedes",
}


class SuppressionReason(str, Enum):
    """Stable, non-sensitive reason codes for consolidation decisions."""

    CANDIDATE_LIMIT = "candidate_limit"
    INACTIVE_FAIL_CLOSED = "inactive_fail_closed"
    SUPERSEDED = "superseded"
    UNVERIFIED_RECOVERY = "unverified_recovery"
    LOWER_AUTHORITY_CONFLICT = "lower_authority_conflict"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    NEAR_DUPLICATE = "near_duplicate"
    NO_INCREMENTAL_VALUE = "no_incremental_value"


@dataclass(frozen=True)
class CandidateSignals:
    """Bounded structured signals extracted from an entry without raw diagnostics."""

    authority_signals: tuple[str, ...] = ()
    relations: tuple[tuple[str, str], ...] = ()
    visited_nodes: int = 0
    node_limit: int = 64
    truncated: bool = False


@dataclass(frozen=True)
class CandidateSuppression:
    entry_id: str
    reason: SuppressionReason
    dominating_candidate_id: str = ""
    chain_key: str = ""
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "reason": self.reason.value,
            "dominating_candidate_id": self.dominating_candidate_id,
            "chain_key": self.chain_key,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ConsolidationResult:
    retained: tuple["RetrievedMemory", ...] = ()
    suppressed: tuple[CandidateSuppression, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def retained_ids(self) -> tuple[str, ...]:
        return tuple(candidate.entry_id for candidate in self.retained)

    @property
    def suppressed_ids(self) -> tuple[str, ...]:
        return tuple(item.entry_id for item in self.suppressed)


@dataclass(frozen=True)
class _Features:
    entry_id: str
    content_tokens: frozenset[str]
    matched_terms: frozenset[str]
    specific_matched_terms: frozenset[str]
    file_keys: frozenset[str]
    domains: frozenset[str]
    authority_signals: frozenset[str]
    relations: tuple[tuple[str, str], ...]
    quantities: frozenset[str]


def _normalize(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().lower()


def _stem(term: str) -> str:
    if not term.isascii() or len(term) < 4:
        return term
    if term.endswith("ies") and len(term) > 4:
        return term[:-3] + "y"
    if term.endswith("ing") and len(term) > 5:
        return term[:-3]
    if term.endswith("ed") and len(term) > 4:
        return term[:-2]
    if term.endswith("es") and len(term) > 4:
        return term[:-2]
    if term.endswith("s") and len(term) > 3:
        return term[:-1]
    return term


def _tokens(value: object, *, limit: int = 256) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(_normalize(value))[:limit]:
        for part in re.split(r"[._:/-]+", raw):
            term = _stem(part)
            if term and term not in seen:
                seen.add(term)
                result.append(term)
    return tuple(result)


def _bounded_targets(value: object, *, limit: int = 16) -> tuple[str, ...]:
    pending = [value]
    targets: list[str] = []
    seen_objects: set[int] = set()
    while pending and len(targets) < limit:
        current = pending.pop(0)
        if isinstance(current, str):
            target = _normalize(current)[:160]
            if target and target not in targets:
                targets.append(target)
            continue
        if isinstance(current, (list, tuple, set, frozenset)):
            object_id = id(current)
            if object_id in seen_objects:
                continue
            seen_objects.add(object_id)
            values = list(current)[:limit]
            pending.extend(sorted(values, key=lambda item: _normalize(item)[:160]))
    return tuple(targets)


def extract_candidate_signals(entry: Any, *, node_limit: int = 64) -> CandidateSignals:
    """Extract bounded authority/relation signals from possibly malformed metadata."""
    authority: set[str] = set()
    relations: set[tuple[str, str]] = set()
    text = " ".join(
        (
            str(getattr(entry, "category", "")),
            str(getattr(entry, "source", "")),
            str(getattr(entry, "tier_reason", "")),
            " ".join(str(item) for item in getattr(entry, "tags", ())[:32]),
            str(getattr(entry, "content", ""))[:2000],
        )
    )
    normalized_text = _normalize(text)
    normalized_content = _normalize(getattr(entry, "content", ""))
    if re.match(r"^canonical\b", normalized_content):
        authority.add("canonical")
    if re.search(r"\b(current|latest)\b|(?:当前|最新)", normalized_text):
        authority.add("current")
    if re.search(r"\b(corrected|correction|user correction)\b|(?:用户纠正|已纠正|更正)", normalized_text):
        authority.add("user_correction")
    if _VERIFIED_AUTHORITY_RE.search(normalized_text):
        authority.add("verified")
    if _UNVERIFIED_RE.search(normalized_text):
        authority.discard("verified")
        authority.add("unverified")
    if re.search(r"\b(deprecated|obsolete|old|superseded)\b|(?:已废弃|旧结论|已取代)", normalized_text):
        authority.add("superseded")
    if getattr(entry, "success_count", 0) > 0:
        authority.add("successful_outcome")

    for target in getattr(entry, "related_to", ())[:32]:
        normalized = _normalize(target)[:160]
        if normalized:
            relations.add(("related_to", normalized))

    roots = (
        ("metadata", getattr(entry, "metadata", {})),
        ("provenance", getattr(entry, "provenance", {})),
    )
    pending: list[tuple[str, object, int]] = [
        (label, value, 0) for label, value in roots if value
    ]
    seen_objects: set[int] = set()
    visited = 0
    truncated = False
    while pending:
        if visited >= node_limit:
            truncated = True
            break
        path, value, depth = pending.pop(0)
        if isinstance(value, (dict, list, tuple, set, frozenset)):
            object_id = id(value)
            if object_id in seen_objects:
                continue
            seen_objects.add(object_id)
        visited += 1
        if depth > 4:
            truncated = True
            continue
        if isinstance(value, dict):
            for raw_key in sorted(value, key=lambda item: _normalize(item)[:80])[:32]:
                key = _normalize(raw_key)[:80]
                nested = value[raw_key]
                if key in {"canonical", "current", "latest", "verified"} and bool(nested):
                    authority.add("current" if key == "latest" else key)
                if key in {"correction", "corrected", "user_correction", "user_corrected"} and bool(nested):
                    authority.add("user_correction")
                if key in {"draft", "possible", "unverified"} and bool(nested):
                    authority.add("unverified")
                if key in {"deprecated", "obsolete", "old", "superseded"} and bool(nested):
                    authority.add("superseded")
                relation = _RELATION_KEYS.get(key)
                if relation:
                    for target in _bounded_targets(nested):
                        if target not in {"true", "false"}:
                            relations.add((relation, target))
                pending.append((f"{path}.{key}", nested, depth + 1))
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = sorted(list(value)[:32], key=lambda item: _normalize(item)[:160])
            pending.extend((f"{path}[]", item, depth + 1) for item in values)
        elif isinstance(value, str):
            normalized = _normalize(value)[:500]
            if normalized in {"canonical", "current", "verified"}:
                authority.add(normalized)
            if normalized in {"corrected", "correction", "user_correction"}:
                authority.add("user_correction")
            if normalized in {"draft", "possible", "unverified"}:
                authority.add("unverified")
    if getattr(entry, "provenance", None):
        authority.add("explicit_provenance")
    return CandidateSignals(
        authority_signals=tuple(sorted(authority)),
        relations=tuple(sorted(relations)),
        visited_nodes=visited,
        node_limit=node_limit,
        truncated=truncated,
    )


def _stable_chain_key(evidence: Iterable[str]) -> str:
    normalized = "\x1f".join(sorted(dict.fromkeys(evidence)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _candidate_features(candidate: "RetrievedMemory") -> _Features:
    text = " ".join(
        (
            candidate.content[:4000],
            candidate.category,
            " ".join(candidate.tags),
        )
    )
    content_tokens = frozenset(_tokens(text))
    matched_terms = frozenset(_stem(_normalize(item)) for item in candidate.score.matched_terms)
    specific = frozenset(term for term in matched_terms if term not in _GENERIC_CHAIN_TERMS)
    files = {
        _normalize(match).replace("\\", "/")
        for match in _FILE_RE.findall(text)
        if match
    }
    quantities = frozenset(_normalize(match) for match in _QUANTITY_RE.findall(candidate.content))
    return _Features(
        entry_id=candidate.entry_id,
        content_tokens=content_tokens,
        matched_terms=matched_terms,
        specific_matched_terms=specific,
        file_keys=frozenset(files),
        domains=frozenset(_normalize(domain) for domain in candidate.domains),
        authority_signals=frozenset(candidate.authority_signals),
        relations=tuple(sorted(candidate.relations)),
        quantities=quantities,
    )


def _authority_score(features: _Features) -> int:
    weights = {
        "canonical": 4,
        "current": 3,
        "explicit_provenance": 1,
        "successful_outcome": 2,
        "user_correction": 5,
        "verified": 3,
        "superseded": -5,
        "unverified": -3,
    }
    return sum(weights.get(signal, 0) for signal in features.authority_signals)


def _relation_between(first: _Features, second: _Features) -> set[str]:
    relations: set[str] = set()
    for relation, target in first.relations:
        if target == second.entry_id:
            relations.add(relation)
    for relation, target in second.relations:
        if target == first.entry_id:
            relations.add(relation)
    return relations


def _chain_evidence(first: _Features, second: _Features) -> tuple[str, ...]:
    evidence: list[str] = []
    relations = _relation_between(first, second)
    evidence.extend(f"relation:{relation}" for relation in sorted(relations))
    shared_files = first.file_keys & second.file_keys
    evidence.extend(f"file:{item}" for item in sorted(shared_files))
    shared_terms = first.specific_matched_terms & second.specific_matched_terms
    if len(shared_terms) >= 2:
        evidence.extend(f"term:{item}" for item in sorted(shared_terms))
    return tuple(evidence)


def _pair_buckets(features: tuple[_Features, ...]) -> set[tuple[str, str]]:
    buckets: dict[str, list[str]] = {}
    known_ids = {item.entry_id for item in features}
    for feature in features:
        for file_key in sorted(feature.file_keys):
            buckets.setdefault(f"file:{file_key}", []).append(feature.entry_id)
        terms = sorted(feature.specific_matched_terms)[:12]
        for first, second in itertools.combinations(terms, 2):
            buckets.setdefault(f"terms:{first}:{second}", []).append(feature.entry_id)
        for relation, target in feature.relations:
            if target in known_ids:
                pair = tuple(sorted((feature.entry_id, target)))
                buckets.setdefault(f"relation:{pair[0]}:{pair[1]}:{relation}", []).extend(pair)
    pairs: set[tuple[str, str]] = set()
    for ids in buckets.values():
        unique = sorted(dict.fromkeys(ids))
        pairs.update(itertools.combinations(unique, 2))
    return pairs


def _near_duplicate(first: _Features, second: _Features, first_text: str, second_text: str) -> bool:
    union = first.content_tokens | second.content_tokens
    jaccard = len(first.content_tokens & second.content_tokens) / len(union) if union else 0.0
    smaller = min(len(first.content_tokens), len(second.content_tokens))
    containment = len(first.content_tokens & second.content_tokens) / smaller if smaller else 0.0
    sequence = SequenceMatcher(None, _normalize(first_text)[:2000], _normalize(second_text)[:2000]).ratio()
    return (jaccard >= 0.45 and containment >= 0.65) or sequence >= 0.82


def _order_within_validated_chains(
    candidates: tuple["RetrievedMemory", ...],
    features_by_id: dict[str, _Features],
    chain_keys: dict[tuple[str, str], str],
) -> tuple["RetrievedMemory", ...]:
    """Apply authority order only inside connected, validated evidence chains."""
    original_index = {
        candidate.entry_id: index for index, candidate in enumerate(candidates)
    }
    adjacency: dict[str, set[str]] = {
        candidate.entry_id: set() for candidate in candidates
    }
    for first_id, second_id in chain_keys:
        adjacency[first_id].add(second_id)
        adjacency[second_id].add(first_id)

    result_ids = [candidate.entry_id for candidate in candidates]
    visited: set[str] = set()
    for root_id in result_ids:
        if root_id in visited or not adjacency[root_id]:
            visited.add(root_id)
            continue
        pending = [root_id]
        component: set[str] = set()
        while pending:
            entry_id = pending.pop()
            if entry_id in component:
                continue
            component.add(entry_id)
            pending.extend(sorted(adjacency[entry_id], reverse=True))
        visited.update(component)
        positions = sorted(original_index[entry_id] for entry_id in component)
        members = sorted(
            component,
            key=lambda entry_id: (
                -_authority_score(features_by_id[entry_id]),
                original_index[entry_id],
                entry_id,
            ),
        )
        for position, entry_id in zip(positions, members, strict=True):
            result_ids[position] = entry_id
    by_id = {candidate.entry_id: candidate for candidate in candidates}
    return tuple(by_id[entry_id] for entry_id in result_ids)


def _direct_conflict(
    first: _Features,
    second: _Features,
    first_text: str,
    second_text: str,
) -> bool:
    relations = _relation_between(first, second)
    if "conflicts_with" in relations:
        return True
    if first.quantities and second.quantities and first.quantities != second.quantities:
        return True
    first_risk = bool(_CONFLICT_RISK_RE.search(first_text))
    second_risk = bool(_CONFLICT_RISK_RE.search(second_text))
    if first_risk != second_risk:
        return True
    first_signals = first.authority_signals
    second_signals = second.authority_signals
    first_is_correction = "user_correction" in first_signals
    second_is_correction = "user_correction" in second_signals
    if first_is_correction != second_is_correction:
        return True
    first_is_unverified = "unverified" in first_signals
    second_is_unverified = "unverified" in second_signals
    if (
        first_is_unverified != second_is_unverified
        and "verified" in (first_signals | second_signals)
    ):
        return True
    return False


def _is_stable_user_constraint(candidate: "RetrievedMemory", features: _Features) -> bool:
    if candidate.scope != "user" or _normalize(candidate.category) not in _USER_CONSTRAINT_CATEGORIES:
        return False
    if "documentation" in features.domains or _DOCUMENTATION_RE.search(candidate.content):
        return False
    if not _CONSTRAINT_RE.search(candidate.content):
        return False
    return bool(features.matched_terms - _NON_OBJECT_TERMS)


def _has_complementary_value(
    candidate: "RetrievedMemory",
    features: _Features,
    retained_features: tuple[_Features, ...],
) -> bool:
    if _is_stable_user_constraint(candidate, features):
        return True
    if candidate.score.file_score > 0 and not any(
        features.file_keys & retained.file_keys for retained in retained_features
    ):
        return True
    covered = set().union(*(item.specific_matched_terms for item in retained_features))
    if features.specific_matched_terms - covered:
        return True
    if "verified" in features.authority_signals and _VERIFICATION_RE.search(candidate.content):
        return True
    return False


class CandidateConsolidator:
    """Consolidate post-gate candidates without retrieval, persistence, or feedback."""

    def __init__(self, *, max_candidates: int = 256) -> None:
        if isinstance(max_candidates, bool) or max_candidates <= 0:
            raise ValueError("max_candidates must be a positive integer")
        self.max_candidates = int(max_candidates)

    def consolidate(
        self,
        candidates: tuple["RetrievedMemory", ...],
        request: "MemoryRetrievalRequest",
    ) -> ConsolidationResult:
        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.rank,
                    -item.score.final_score,
                    item.entry_id,
                ),
            )
        )
        bounded = ordered[: self.max_candidates]
        overflow = ordered[self.max_candidates :]
        by_id = {candidate.entry_id: candidate for candidate in bounded}
        features_by_id = {
            candidate.entry_id: _candidate_features(candidate) for candidate in bounded
        }
        suppressions: dict[str, CandidateSuppression] = {
            candidate.entry_id: CandidateSuppression(
                entry_id=candidate.entry_id,
                reason=SuppressionReason.CANDIDATE_LIMIT,
                reason_codes=("bounded_before_pairwise_comparison",),
            )
            for candidate in overflow
        }

        pairs = _pair_buckets(tuple(features_by_id.values()))
        chain_keys: dict[tuple[str, str], str] = {}
        for pair in sorted(pairs):
            first = features_by_id[pair[0]]
            second = features_by_id[pair[1]]
            evidence = _chain_evidence(first, second)
            if evidence:
                chain_keys[pair] = _stable_chain_key(evidence)
        bounded = _order_within_validated_chains(
            bounded,
            features_by_id,
            chain_keys,
        )

        # Structured authority/conflict/duplicate decisions are restricted to
        # pairs that passed deterministic evidence-chain validation.
        for pair, chain_key in sorted(chain_keys.items()):
            first_id, second_id = pair
            if first_id in suppressions or second_id in suppressions:
                continue
            first = features_by_id[first_id]
            second = features_by_id[second_id]
            first_candidate = by_id[first_id]
            second_candidate = by_id[second_id]
            first_score = _authority_score(first)
            second_score = _authority_score(second)

            if any(
                relation == "superseded_by" and target == second_id
                for relation, target in first.relations
            ) or any(
                relation == "supersedes" and target == first_id
                for relation, target in second.relations
            ):
                suppressions[first_id] = CandidateSuppression(
                    first_id,
                    SuppressionReason.SUPERSEDED,
                    second_id,
                    chain_key,
                    ("explicit_supersession",),
                )
                continue
            if any(
                relation == "superseded_by" and target == first_id
                for relation, target in second.relations
            ) or any(
                relation == "supersedes" and target == second_id
                for relation, target in first.relations
            ):
                suppressions[second_id] = CandidateSuppression(
                    second_id,
                    SuppressionReason.SUPERSEDED,
                    first_id,
                    chain_key,
                    ("explicit_supersession",),
                )
                continue

            conflict = _direct_conflict(
                first,
                second,
                first_candidate.content,
                second_candidate.content,
            )
            if conflict and first_score == second_score:
                suppressions[first_id] = CandidateSuppression(
                    first_id,
                    SuppressionReason.UNRESOLVED_CONFLICT,
                    "",
                    chain_key,
                    ("equal_authority_conflict",),
                )
                suppressions[second_id] = CandidateSuppression(
                    second_id,
                    SuppressionReason.UNRESOLVED_CONFLICT,
                    "",
                    chain_key,
                    ("equal_authority_conflict",),
                )
                continue
            if conflict and first_score != second_score:
                winner_id, loser_id = (
                    (first_id, second_id) if first_score > second_score else (second_id, first_id)
                )
                loser = features_by_id[loser_id]
                winner = features_by_id[winner_id]
                reason = (
                    SuppressionReason.UNVERIFIED_RECOVERY
                    if (
                        "unverified" in loser.authority_signals
                        and "verified" in winner.authority_signals
                    )
                    else SuppressionReason.LOWER_AUTHORITY_CONFLICT
                )
                suppressions[loser_id] = CandidateSuppression(
                    loser_id,
                    reason,
                    winner_id,
                    chain_key,
                    ("structured_conflict", "authority_difference"),
                )
                continue

            if (
                first_score != second_score
                and _near_duplicate(
                    first,
                    second,
                    first_candidate.content,
                    second_candidate.content,
                )
                and max(first_score, second_score) >= 3
            ):
                winner_id, loser_id = (
                    (first_id, second_id) if first_score > second_score else (second_id, first_id)
                )
                suppressions[loser_id] = CandidateSuppression(
                    loser_id,
                    SuppressionReason.NEAR_DUPLICATE,
                    winner_id,
                    chain_key,
                    ("same_chain", "higher_authority", "low_information_gain"),
                )

        retained: list["RetrievedMemory"] = []
        retained_features: list[_Features] = []
        request_files = tuple(_normalize(path).replace("\\", "/") for path in request.current_files)
        request_basenames = tuple(path.split("/")[-1] for path in request_files)
        for candidate in bounded:
            if candidate.entry_id in suppressions:
                continue
            features = features_by_id[candidate.entry_id]
            if not retained:
                retained.append(candidate)
                retained_features.append(features)
                continue

            primary = retained[0]
            primary_features = retained_features[0]
            pair = tuple(sorted((primary.entry_id, candidate.entry_id)))
            complementary = _has_complementary_value(
                candidate,
                features,
                tuple(retained_features),
            )
            file_bound_collision = (
                primary.score.file_score > 0
                and candidate.score.file_score == 0
                and candidate.scope != "user"
                and len(features.matched_terms) <= 1
                and any(
                    term in basename
                    for term in features.matched_terms
                    for basename in request_basenames
                )
            )
            domain_gap = primary.score.domain_score - candidate.score.domain_score
            weak_context = (
                candidate.score.file_score == 0
                and len(primary_features.matched_terms) >= len(features.matched_terms)
                and not complementary
                and (
                    file_bound_collision
                    or (
                        _normalize(candidate.category) in _NOISE_CATEGORIES
                        and (
                            domain_gap > 0
                            or bool(_DOCUMENTATION_RE.search(candidate.content))
                            or len(primary_features.matched_terms)
                            >= len(features.matched_terms) + 1
                        )
                    )
                )
            )
            if weak_context:
                suppressions[candidate.entry_id] = CandidateSuppression(
                    candidate.entry_id,
                    SuppressionReason.NO_INCREMENTAL_VALUE,
                    primary.entry_id,
                    chain_keys.get(pair, ""),
                    ("primary_more_task_specific", "no_independent_applicable_value"),
                )
                continue
            retained.append(candidate)
            retained_features.append(features)

        order = {candidate.entry_id: index for index, candidate in enumerate(ordered)}
        suppressed = tuple(
            sorted(
                suppressions.values(),
                key=lambda item: (order.get(item.entry_id, len(order)), item.entry_id),
            )
        )
        reason_counts: dict[str, int] = {}
        for item in suppressed:
            reason_counts[item.reason.value] = reason_counts.get(item.reason.value, 0) + 1
        return ConsolidationResult(
            retained=tuple(retained),
            suppressed=suppressed,
            diagnostics={
                "input_count": len(ordered),
                "bounded_count": len(bounded),
                "retained_count": len(retained),
                "suppressed_count": len(suppressed),
                "candidate_limit": self.max_candidates,
                "pair_bucket_count": len(pairs),
                "validated_chain_pair_count": len(chain_keys),
                "reason_counts": dict(sorted(reason_counts.items())),
                "complexity_bound": "O(B^2) after deterministic bucketing; B<=candidate_limit",
            },
        )
