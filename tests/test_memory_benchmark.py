"""Memory system performance benchmarks."""

import time

import pytest

from minicode.memory import MemoryScope


@pytest.mark.benchmark
def test_search_performance_100_entries(memory_manager, monkeypatch):
    """Test search performance with 100 entries."""
    from tests.test_helpers import create_memory_entries
    create_memory_entries(memory_manager, 100)

    # Measure ranking rather than filesystem persistence. The default
    # record_usage=True intentionally locks and saves selected scopes, whose
    # latency depends on the host filesystem and made this sub-second unit
    # benchmark flaky under full-suite load.
    memory_manager.search("testing", record_usage=False)
    start = time.perf_counter()
    for _ in range(10):
        memory_manager.search("testing", record_usage=False)
    elapsed = time.perf_counter() - start

    # Should complete 10 searches in under 1 second
    assert elapsed < 1.0, f"Search too slow: {elapsed:.3f}s for 10 searches with 100 entries"

    # A durable usage update must preserve the full persistence boundary, but
    # unchanged entries should cross it once rather than being serialized and
    # recursively sanitized twice in the same save.
    from minicode.memory import MemoryEntry

    original_to_dict = MemoryEntry.to_dict
    serialized_ids: list[str] = []

    def counted_to_dict(entry):
        serialized_ids.append(entry.id)
        return original_to_dict(entry)

    monkeypatch.setattr(MemoryEntry, "to_dict", counted_to_dict)
    memory_manager.search("testing")

    project_entries = memory_manager.memories[MemoryScope.PROJECT].entries
    assert len(project_entries) == 100
    assert serialized_ids == [entry.id for entry in project_entries]


@pytest.mark.benchmark
def test_search_performance_500_entries(memory_manager):
    """Test search performance with 500 entries."""
    from tests.test_helpers import create_memory_entries
    create_memory_entries(memory_manager, 500)

    memory_manager.search("architecture", record_usage=False)
    start = time.perf_counter()
    for _ in range(10):
        memory_manager.search("architecture", record_usage=False)
    elapsed = time.perf_counter() - start

    # Should complete 10 searches in under 5 seconds
    assert elapsed < 5.0, f"Search too slow: {elapsed:.3f}s for 10 searches with 500 entries"


@pytest.mark.benchmark
def test_chinese_search_performance(memory_manager):
    """Test Chinese search performance."""
    from tests.test_helpers import create_chinese_memory_entries
    create_chinese_memory_entries(memory_manager, 50)

    memory_manager.search("测试", record_usage=False)
    start = time.perf_counter()
    for _ in range(10):
        memory_manager.search("测试", record_usage=False)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"Chinese search too slow: {elapsed:.3f}s"


def test_memory_size_limits(memory_manager):
    """Test that memory respects size limits."""
    # Add entries until limit is hit
    for i in range(250):  # More than max_entries (200)
        memory_manager.add_entry(
            MemoryScope.PROJECT,
            "test",
            f"Entry {i} with some content to take up space" * 10,
        )
    
    entries = memory_manager.memories[MemoryScope.PROJECT].entries
    assert len(entries) <= 200, f"Too many entries: {len(entries)}"
