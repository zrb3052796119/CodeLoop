from __future__ import annotations

import minicode.memory as memory_module
from minicode.memory import MemoryEntry, MemoryFile, MemoryScope


def _entry(entry_id: str, content: str) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        scope=MemoryScope.PROJECT,
        category="test",
        content=content,
        approval_status="approved",
        lifecycle_status="active",
        safety_status="safe",
    )


def test_search_uses_current_entries_after_index_mutations() -> None:
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        max_entries=10,
        max_size_bytes=10_000,
    )
    first = _entry("first", "pytest fixture isolation rule")
    second = _entry("second", "docker network recovery rule")
    memory_file.add_entry(first)
    memory_file.add_entry(second)

    assert memory_file.search("pytest fixture", record_usage=False)[0] is first

    assert memory_file.update_entry(
        first.id,
        "unrelated formatting preference",
    )
    assert memory_file.update_entry(
        second.id,
        "pytest fixture isolation rule",
    )

    assert memory_file.search("pytest fixture", record_usage=False)[0] is second
    assert memory_file.delete_entry(second.id)
    assert memory_file.search("pytest fixture", record_usage=False) == []


def test_index_rebuild_does_not_compute_unused_bm25_statistics(
    monkeypatch,
) -> None:
    memory_file = MemoryFile(
        scope=MemoryScope.PROJECT,
        entries=[_entry("entry", "pytest fixture isolation rule")],
    )

    def forbidden(_documents):
        raise AssertionError(
            "index-only rebuild must not compute query-time BM25 statistics"
        )

    monkeypatch.setattr(memory_module, "_compute_idf", forbidden)
    monkeypatch.setattr(memory_module, "_compute_avgdl", forbidden)

    memory_file._ensure_cache_valid()

    assert memory_file.get_entries_by_category("test") == [
        memory_file.entries[0]
    ]

