"""Versioned lexical policies used by persistent-Memory retrieval.

The canonical retriever and post-gate candidate consolidator deliberately use
different lexical semantics.  Keeping both profiles at one seam makes that
difference explicit without silently changing either production contract.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

_RETRIEVAL_SPACE_RE = re.compile(r"\s+")
_RETRIEVAL_SEPARATOR_RE = re.compile(r"[_./\\:-]+")
_CONSOLIDATION_TOKEN_RE = re.compile(
    r"[a-z0-9][a-z0-9_.:/-]*|[\u3400-\u9fff]+",
    re.IGNORECASE,
)
_CONSOLIDATION_PART_RE = re.compile(r"[._:/-]+")
_RETRIEVAL_V2_TOKEN_RE = re.compile(
    r"[a-z0-9]+|[\u3400-\u9fff]+",
    re.IGNORECASE,
)
_SINGLE_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_CJK_POLARITY_MARKERS = (
    "不得",
    "不要",
    "不能",
    "不可",
    "无需",
    "禁止",
    "必须",
    "务必",
    "应当",
    "应该",
    "不",
    "无",
    "未",
    "非",
)
_ASCII_POLARITY_MARKERS = (
    "must not",
    "do not",
    "should not",
    "without",
    "never",
    "forbid",
    "prohibit",
    "must",
    "require",
)
_CJK_POLARITY_RE = re.compile(
    "|".join(re.escape(marker) for marker in _CJK_POLARITY_MARKERS)
)
_ASCII_POLARITY_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(marker) for marker in _ASCII_POLARITY_MARKERS)
    + r")\b",
    re.IGNORECASE,
)


class MemoryLexicalProfile(str, Enum):
    """Stable identities for independently versioned lexical behavior."""

    RETRIEVAL_V1 = "canonical_retrieval_v1"
    RETRIEVAL_V2 = "canonical_retrieval_v2"
    CONSOLIDATION_V1 = "candidate_consolidation_v1"


def _stem_ascii_suffix(
    term: str,
    *,
    restore_ing_after: tuple[str, ...],
    restore_ed_after: tuple[str, ...],
) -> str:
    if not term.isascii() or len(term) < 4:
        return term
    if term.endswith("ies") and len(term) > 4:
        return term[:-3] + "y"
    if term.endswith("ing") and len(term) > 5:
        base = term[:-3]
        return base + "e" if base.endswith(restore_ing_after) else base
    if term.endswith("ed") and len(term) > 4:
        base = term[:-2]
        return base + "e" if base.endswith(restore_ed_after) else base
    if term.endswith("es") and len(term) > 4:
        return term[:-2]
    if term.endswith("s") and len(term) > 3:
        return term[:-1]
    return term


@dataclass(frozen=True, slots=True)
class RetrievalLexicalPolicyV1:
    """Canonical retrieval's frozen lexical behavior."""

    profile: MemoryLexicalProfile = field(
        default=MemoryLexicalProfile.RETRIEVAL_V1,
        init=False,
    )

    @staticmethod
    def normalize_input(value: object) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        return _RETRIEVAL_SPACE_RE.sub(" ", text).strip()

    def normalize_match(self, value: object) -> str:
        normalized = self.normalize_input(value).lower()
        separated = _RETRIEVAL_SEPARATOR_RE.sub(" ", normalized)
        return _RETRIEVAL_SPACE_RE.sub(" ", separated).strip()

    def tokenize_match(self, value: object) -> list[str]:
        # Import the owning module at call time.  Prepared-corpus work tests
        # intentionally monkeypatch this private compatibility seam.
        import minicode.memory as memory_module

        return memory_module._tokenize(self.normalize_match(value))

    @staticmethod
    def expand_terms(
        terms: list[str],
        *,
        active_domains: Iterable[str] | None = None,
    ) -> list[str]:
        # Keep expansion order and duplicates: manager-compatible BM25 uses
        # both as part of its established scoring contract.
        import minicode.memory as memory_module

        domains = (
            list(active_domains)
            if active_domains is not None
            else None
        )
        return memory_module._expand_query_terms(
            list(terms),
            active_domains=domains or None,
        )

    def term_set(
        self,
        value: object,
        *,
        active_domains: Iterable[str] | None = None,
    ) -> set[str]:
        expanded = self.expand_terms(
            self.tokenize_match(value),
            active_domains=active_domains,
        )
        return {
            self.stem(token.lower())
            for token in expanded
            if token.strip()
        }

    @staticmethod
    def stem(term: str) -> str:
        return _stem_ascii_suffix(
            term,
            restore_ing_after=("c", "v"),
            restore_ed_after=("c", "u", "v"),
        )


@dataclass(frozen=True, slots=True)
class RetrievalLexicalPolicyV2:
    """Offset-invariant CJK features with separate recall and gate lanes.

    Candidate terms retain individual CJK characters for broad discovery.
    Admission terms require an ASCII token or a sliding CJK bigram, preventing
    one common character from independently authorizing prompt injection.
    Polarity markers are exposed separately so that this safety filtering does
    not erase negation or obligation evidence.
    """

    profile: MemoryLexicalProfile = field(
        default=MemoryLexicalProfile.RETRIEVAL_V2,
        init=False,
    )

    @staticmethod
    def normalize_input(value: object) -> str:
        return RetrievalLexicalPolicyV1.normalize_input(value)

    def normalize_match(self, value: object) -> str:
        return RetrievalLexicalPolicyV1().normalize_match(value)

    def tokenize_match(self, value: object) -> list[str]:
        tokens: list[str] = []
        for raw in _RETRIEVAL_V2_TOKEN_RE.findall(self.normalize_match(value)):
            token = raw.lower()
            if token.isascii():
                tokens.append(token)
                continue
            tokens.extend(token)
            tokens.extend(
                token[index : index + 2]
                for index in range(len(token) - 1)
            )
        return tokens

    def admission_tokens(self, value: object) -> list[str]:
        return [
            token
            for token in self.tokenize_match(value)
            if not self._is_single_cjk(token)
        ]

    @staticmethod
    def stem(term: str) -> str:
        return RetrievalLexicalPolicyV1.stem(term)

    @staticmethod
    def expand_terms(
        terms: list[str],
        *,
        active_domains: Iterable[str] | None = None,
    ) -> list[str]:
        return RetrievalLexicalPolicyV1.expand_terms(
            terms,
            active_domains=active_domains,
        )

    @staticmethod
    def _is_single_cjk(term: str) -> bool:
        return _SINGLE_CJK_RE.fullmatch(term) is not None

    def candidate_term_set(
        self,
        value: object,
        *,
        active_domains: Iterable[str] | None = None,
    ) -> set[str]:
        """Return broad terms suitable for candidate discovery only."""
        expanded = self.expand_terms(
            self.tokenize_match(value),
            active_domains=active_domains,
        )
        return {
            self.stem(token.lower())
            for token in expanded
            if token.strip()
        }

    def admission_terms(
        self,
        value: object,
        *,
        active_domains: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        """Return ordered relevance evidence that excludes CJK singletons."""
        return self.expand_admission_tokens(
            self.admission_tokens(value),
            active_domains=active_domains,
        )

    def expand_admission_tokens(
        self,
        tokens: Iterable[str],
        *,
        active_domains: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        expanded = self.expand_terms(
            list(tokens),
            active_domains=active_domains,
        )
        result: list[str] = []
        seen: set[str] = set()
        for token in expanded:
            term = self.stem(token.lower())
            if (
                not term.strip()
                or self._is_single_cjk(term)
                or term in seen
            ):
                continue
            seen.add(term)
            result.append(term)
        return tuple(result)

    def admission_term_set(
        self,
        value: object,
        *,
        active_domains: Iterable[str] | None = None,
    ) -> set[str]:
        return set(
            self.admission_terms(
                value,
                active_domains=active_domains,
            )
        )

    def polarity_markers(self, value: object) -> tuple[str, ...]:
        """Return explicit negation/obligation lexemes in source order."""
        normalized = self.normalize_input(value).lower()
        matches = [
            (match.start(), match.group(0).lower())
            for pattern in (_CJK_POLARITY_RE, _ASCII_POLARITY_RE)
            for match in pattern.finditer(normalized)
        ]
        matches.sort(key=lambda item: (item[0], item[1]))
        return tuple(marker for _offset, marker in matches)


@dataclass(frozen=True, slots=True)
class ConsolidationLexicalPolicyV1:
    """Post-gate candidate consolidation's frozen lexical behavior."""

    profile: MemoryLexicalProfile = field(
        default=MemoryLexicalProfile.CONSOLIDATION_V1,
        init=False,
    )

    @staticmethod
    def normalize_scalar(value: object) -> str:
        return unicodedata.normalize("NFKC", str(value or "")).strip().lower()

    @staticmethod
    def stem(term: str) -> str:
        return _stem_ascii_suffix(
            term,
            restore_ing_after=(),
            restore_ed_after=(),
        )

    def unique_tokens(self, value: object, *, limit: int = 256) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        normalized = self.normalize_scalar(value)
        for raw in _CONSOLIDATION_TOKEN_RE.findall(normalized)[:limit]:
            for part in _CONSOLIDATION_PART_RE.split(raw):
                term = self.stem(part)
                if term and term not in seen:
                    seen.add(term)
                    result.append(term)
        return tuple(result)


RETRIEVAL_LEXICAL_V1 = RetrievalLexicalPolicyV1()
RETRIEVAL_LEXICAL_V2 = RetrievalLexicalPolicyV2()
CONSOLIDATION_LEXICAL_V1 = ConsolidationLexicalPolicyV1()


__all__ = [
    "CONSOLIDATION_LEXICAL_V1",
    "RETRIEVAL_LEXICAL_V1",
    "RETRIEVAL_LEXICAL_V2",
    "ConsolidationLexicalPolicyV1",
    "MemoryLexicalProfile",
    "RetrievalLexicalPolicyV1",
    "RetrievalLexicalPolicyV2",
]
