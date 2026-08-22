from __future__ import annotations

from minicode.context_manager import ContextManager


def test_context_accounting_uses_canonical_85_percent_high_water_mark() -> None:
    manager = ContextManager(model="default", context_window=1_000)
    manager.add_message({"role": "user", "content": "x" * 3_600})

    assert 85.0 <= manager.get_stats().usage_percentage < 95.0
    assert manager.should_auto_compact() is True


def test_context_manager_compatibility_interface_delegates_to_context_compactor() -> None:
    manager = ContextManager(model="default", context_window=1_000)
    manager.add_message({"role": "system", "content": "SYSTEM"})
    for index in range(30):
        manager.add_message(
            {"role": "user", "content": f"constraint-{index} " + "x" * 180}
        )
        manager.add_message(
            {"role": "assistant", "content": f"result-{index} " + "y" * 180}
        )

    compacted = manager.compact_messages()

    assert any(message.get("_compact_boundary") is True for message in compacted)
    assert manager.compaction_history[-1]["backend"] == "context_compactor"
