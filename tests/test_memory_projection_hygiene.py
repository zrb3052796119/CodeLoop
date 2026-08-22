from __future__ import annotations

import json
from pathlib import Path

from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryManager,
    MemoryScope,
    MemoryTier,
)
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest


def test_memory_markdown_projects_only_authoritative_active_entries(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    visible = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Authoritative pytest rule stays visible.",
    )
    pending = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Pending pytest claim must remain review-only.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    rejected = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Rejected pytest claim must remain audit-only.",
    )
    archival = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Archived pytest claim must remain audit-only.",
        tier=MemoryTier.ARCHIVAL,
        lifecycle_status="deprecated",
    )
    assert visible is not None
    assert pending is not None
    assert rejected is not None
    assert archival is not None

    manager.reject_entry(rejected.id, reason="wrong rule")

    scope_root = tmp_path / ".mini-code-memory"
    markdown = (scope_root / "MEMORY.md").read_text(encoding="utf-8")
    authority = json.loads((scope_root / "memory.json").read_text(encoding="utf-8"))

    assert visible.content in markdown
    assert pending.content not in markdown
    assert rejected.content not in markdown
    assert archival.content not in markdown
    assert {entry["content"] for entry in authority["entries"]} >= {
        visible.content,
        pending.content,
        rejected.content,
        archival.content,
    }


def test_explicit_negative_feedback_reduces_canonical_memory_score(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(project_root=tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Invoice retry tests use a bounded failure schedule.",
        tags=["invoice", "retry", "tests"],
    )
    assert entry is not None
    request = MemoryRetrievalRequest(query="invoice retry tests")

    before = CanonicalMemoryRetriever(manager).retrieve(request)
    manager.record_corroborated_feedback([entry.id], success=False)
    after = CanonicalMemoryRetriever(manager).retrieve(request)

    assert before.candidates[0].entry_id == entry.id
    assert after.candidates[0].entry_id == entry.id
    assert after.candidates[0].score.corroborated_score < 0
    assert (
        after.candidates[0].score.final_score
        < before.candidates[0].score.final_score
    )
