"""Directory-level semantic preselection for hierarchical Skill discovery.

One embedding vector per directory instead of one per skill: a thousand-skill
catalog across ~66 directories becomes a bounded 66-vector index, not the
routed arm's full-catalog warm-up. The signal is advisory in the same sense
as the intent hints it extends -- it only reorders and extends the suggested
starting directories, never ``prompt_skills``, the load policy, or authority.

The directory text is built from catalog data that is already authorized for
the turn, and every failure path is fail-closed: an unavailable transport or
a malformed score simply yields no semantic candidates and the lexical
``AliasSemanticMatcher.query_coverage`` ranking stands alone.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from minicode.skill_semantics import (
    EmbeddingSemanticMatcher,
    EmbeddingUnavailable,
    embedding_thresholds,
)

logger = logging.getLogger(__name__)

_PRESSEL_RETRY_COOLDOWN_SECONDS = 300.0
_MAX_DIRECTORY_TEXT_CHARS = 1_600
_MAX_DIRECTORY_SKILL_NAMES = 40
# A hierarchical catalog has few directories by construction, so the first
# presel turn may warm the whole directory index at once instead of the
# per-route skill-vector trickle.
_MAX_NEW_DIRECTORY_VECTORS = 128

SourceMap = dict[str, str]


def directory_presel_text(
    directory: str,
    description: str,
    skill_names: list[str],
) -> str:
    """Bounded embedding text for one directory card.

    Skill qualified names carry most of the discriminative vocabulary a
    directory card hides, so they are listed explicitly; the combined text
    stays small enough that 66 directories embed in a handful of batches.
    """
    parts = [directory, description]
    names = skill_names[:_MAX_DIRECTORY_SKILL_NAMES]
    if names:
        parts.append("Skills: " + ", ".join(names))
    text = "\n".join(part for part in parts if part)
    return text[:_MAX_DIRECTORY_TEXT_CHARS]


def blend_directory_suggestions(
    lexical_scores: Mapping[str, float],
    semantic_scores: Mapping[str, float],
    *,
    signal_threshold: float,
    limit: int,
) -> tuple[list[str], SourceMap]:
    """Merge lexical and semantic directory scores into one ranked list.

    A directory qualifies when either signal fires; rank by the stronger of
    the two. Returns the ranked names plus a per-name provenance map so the
    routing result can show where each suggestion came from.
    """
    combined: dict[str, float] = {}
    sources: SourceMap = {}
    for directory in sorted(set(lexical_scores) | set(semantic_scores)):
        lexical = float(lexical_scores.get(directory, 0.0))
        semantic = float(semantic_scores.get(directory, 0.0))
        if lexical <= 0.0 and semantic < signal_threshold:
            continue
        combined[directory] = max(lexical, semantic)
        if lexical > 0.0 and semantic >= signal_threshold:
            sources[directory] = "lexical+semantic"
        elif lexical > 0.0:
            sources[directory] = "lexical"
        else:
            sources[directory] = "semantic"
    ranked = [
        directory
        for directory, _score in sorted(
            combined.items(), key=lambda item: (-item[1], item[0])
        )[: max(0, limit)]
    ]
    return ranked, {name: sources[name] for name in ranked}


class DirectoryPreselMatcher:
    """Cosine ranking of directories, with its own cache and cooldown.

    A separate cache file keeps directory vectors from competing with the
    routed arm's skill vectors for entries. After one transport failure the
    matcher stops calling the endpoint for a cooldown, mirroring the router's
    circuit breaker.
    """

    def __init__(
        self,
        embedding: Any,
        *,
        signal_threshold: float,
    ) -> None:
        self._embedding = embedding
        self._signal_threshold = float(signal_threshold)
        self._retry_after = 0.0

    @classmethod
    def from_environment(
        cls, workspace: str | Path
    ) -> "DirectoryPreselMatcher | None":
        """Build from trusted embedding settings, or ``None`` when off.

        The transport is the same OpenAI-compatible embedding client the
        routed arm uses; only the cache file and thresholds differ.
        """
        from minicode.embeddings import (
            create_openai_compatible_embedding_client,
        )

        try:
            client = create_openai_compatible_embedding_client(workspace)
        except EmbeddingUnavailable as error:
            logger.warning(
                "Directory presel configuration unavailable; lexical "
                "suggestions only: %s",
                error,
            )
            return None
        if client is None:
            return None
        root = Path(workspace) / ".mini-code"
        embedding = EmbeddingSemanticMatcher(
            client,
            root / "skill-directory-embeddings.json",
            cache_anchor=workspace,
        )
        signal_threshold = embedding_thresholds(workspace)[0]
        return cls(embedding, signal_threshold=signal_threshold)

    @property
    def signal_threshold(self) -> float:
        return self._signal_threshold

    def rank(
        self,
        query: str,
        by_directory: Mapping[str, str],
        *,
        call_context: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        """Query→directory cosine map, or ``{}`` on any fail-closed path."""
        if not query.strip() or not by_directory:
            return {}
        now = time.time()
        if now < self._retry_after:
            return {}
        # Cache keys are content digests: directory names can exceed the
        # 64-char key cap, so the text itself is what identifies the vector.
        text_by_directory = {
            directory: text.strip()
            for directory, text in sorted(by_directory.items())
            if text.strip()
        }
        if not text_by_directory:
            return {}
        pairs = [
            (
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                text,
            )
            for text in text_by_directory.values()
        ]
        try:
            similarities = self._embedding.similarities(
                query,
                pairs,
                call_context=call_context,
                max_new_vectors=_MAX_NEW_DIRECTORY_VECTORS,
            )
        except EmbeddingUnavailable as error:
            self._retry_after = time.time() + _PRESSEL_RETRY_COOLDOWN_SECONDS
            logger.warning(
                "Directory presel embedding unavailable (%s); lexical "
                "suggestions only for %.0fs",
                error,
                _PRESSEL_RETRY_COOLDOWN_SECONDS,
            )
            return {}
        return {
            directory: float(similarity)
            for (directory, _text), similarity in zip(
                text_by_directory.items(), similarities
            )
            if similarity > 0.0
        }


__all__ = [
    "DirectoryPreselMatcher",
    "blend_directory_suggestions",
    "directory_presel_text",
]
