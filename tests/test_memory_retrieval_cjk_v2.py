from __future__ import annotations

from pathlib import Path

from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_retrieval import CanonicalMemoryRetriever, MemoryRetrievalRequest


def _approved_project_memory(
    tmp_path: Path,
    content: str,
) -> tuple[MemoryManager, str]:
    manager = MemoryManager(
        project_root=tmp_path / "workspace",
        data_root=tmp_path / "memory-home",
    )
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        content,
        approval_policy=MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED,
    )
    assert entry is not None
    return manager, entry.id


def test_canonical_retrieval_uses_offset_invariant_cjk_admission_terms(
    tmp_path: Path,
) -> None:
    manager, entry_id = _approved_project_memory(tmp_path, "请认证策略文件")

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(query="认证策略文件")
    )

    assert result.rendered_ids == (entry_id,)
    assert result.diagnostics["lexical_profile"] == "canonical_retrieval_v2"


def test_single_cjk_character_cannot_independently_pass_canonical_gate(
    tmp_path: Path,
) -> None:
    manager, _entry_id = _approved_project_memory(tmp_path, "类")

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(query="类")
    )

    assert result.no_match is True
    assert result.no_match_reason == "query_has_no_informative_terms"
    assert result.candidate_ids == ()
    assert result.diagnostics["lexical_profile"] == "canonical_retrieval_v2"


def test_canonical_diagnostics_preserve_query_polarity_markers(
    tmp_path: Path,
) -> None:
    manager, entry_id = _approved_project_memory(
        tmp_path,
        "不删除缓存，必须保留索引",
    )

    result = CanonicalMemoryRetriever(manager).retrieve(
        MemoryRetrievalRequest(query="不删除缓存，必须保留索引")
    )

    assert result.rendered_ids == (entry_id,)
    assert result.diagnostics["query_polarity_markers"] == ["不", "必须"]
