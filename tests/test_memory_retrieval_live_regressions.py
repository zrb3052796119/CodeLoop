from __future__ import annotations

from pathlib import Path

from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
from minicode.memory_retrieval import (
    CanonicalMemoryRetriever,
    MemoryRetrievalRequest,
    RetrievalSource,
)


def _manager(tmp_path: Path, entry: MemoryEntry) -> MemoryManager:
    manager = MemoryManager(project_root=tmp_path)
    manager.memories[entry.scope].entries.append(entry)
    for memory_file in manager.memories.values():
        memory_file._rebuild_indices()
    return manager


def _entry(
    content: str,
    *,
    category: str,
    tags: list[str] | None = None,
    domains: list[str] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id="audit-token-policy",
        scope=MemoryScope.PROJECT,
        category=category,
        content=content,
        tags=list(tags or []),
        domains=list(domains or []),
        tier=MemoryTier.SHORT_TERM,
        lifecycle_status="active",
        approval_status="approved",
        safety_status="safe",
        created_at=1_700_000_000.0,
        updated_at=1_700_000_000.0,
        last_accessed=1_700_000_000.0,
    )


def _request(query: str) -> MemoryRetrievalRequest:
    return MemoryRetrievalRequest(
        query=query,
        context_usage=0.4,
        max_memories=5,
        max_total_tokens=800,
        max_tokens_per_memory=160,
        min_relevance=0.0,
        source_entrypoint=RetrievalSource.CANONICAL,
    )


def test_unrelated_deployment_question_does_not_render_security_constraint(
    tmp_path: Path,
) -> None:
    entry = _entry(
        (
            "Project constraint: Every outbound audit-event correlation token must "
            "use `ZETA-` followed by exactly four uppercase hexadecimal characters "
            "(`0-9` or `A-F`). This rule applies only to outbound audit-event "
            "correlation tokens. It does not apply to database IDs, user IDs, or "
            "internal trace IDs."
        ),
        category="task_context",
        tags=["self-reflection", "success", "list_files", "read_file"],
        domains=["security"],
    )

    result = CanonicalMemoryRetriever(_manager(tmp_path, entry)).retrieve(
        _request(
            "No tools. Which cloud deployment region is configured for this project? "
            "Use only approved durable project memory already supplied in context. "
            "If unsupported, answer exactly UNKNOWN."
        )
    )

    assert result.no_match is True, {
        "informative": result.diagnostics.get("informative_terms"),
        "matched": result.candidates[0].score.matched_terms,
        "reasons": result.candidates[0].reason_codes,
        "score": result.candidates[0].score.final_score,
    }
    assert result.rendered_ids == ()


def test_audit_domain_signal_recovers_low_overlap_security_constraint(
    tmp_path: Path,
) -> None:
    entry = _entry(
        (
            "Every outbound audit-event correlation token uses ZETA- followed by "
            "exactly four uppercase hexadecimal characters. Database IDs, user IDs, "
            "and internal trace IDs are outside this rule."
        ),
        category="constraint",
        domains=["security"],
    )

    result = CanonicalMemoryRetriever(_manager(tmp_path, entry)).retrieve(
        _request(
            "During compliance export, what shape should the tracking marker "
            "attached to each emitted audit record have, and which other identifier "
            "families are exempt?"
        )
    )

    assert result.rendered_ids == (entry.id,)
    assert result.diagnostics["active_domains"] == ["security"]
    assert "domain_with_lexical_evidence" in result.rendered[0].reason_codes
