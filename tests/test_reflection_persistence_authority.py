from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_mod
from minicode.agent_reflection import ReflectionEngine
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


def _manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MemoryManager:
    monkeypatch.setattr(
        memory_mod,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return MemoryManager(project_root=workspace)


def _verified_recovery_trace() -> list[dict[str, object]]:
    return [
        {
            "event_id": "event-1",
            "call_id": "call-1",
            "type": "error",
            "tool_name": "run_command",
            "error_type": "AssertionError",
            "message": "Parser returned the wrong token",
        },
        {
            "event_id": "event-2",
            "call_id": "call-2",
            "type": "recovery",
            "tool_name": "edit_file",
            "related_error_call_ids": ["call-1"],
            "action": "Corrected token normalization",
            "files_changed": ["src/parser.py"],
        },
        {
            "event_id": "event-3",
            "call_id": "call-3",
            "type": "tool_call",
            "tool_name": "run_command",
            "command": "pytest tests/test_parser.py -q",
        },
        {
            "event_id": "event-4",
            "call_id": "call-3",
            "type": "tool_result",
            "tool_name": "run_command",
            "status": "success",
            "output_summary": "7 passed",
        },
        {"event_id": "event-5", "type": "task_result", "status": "success"},
    ]


def test_legacy_direct_persistence_flag_is_rejected_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match=r"MemoryPipeline\.write"):
        ReflectionEngine(manager, persist_reflections=True)

    assert manager.memories[MemoryScope.PROJECT].entries == []


def test_injected_engine_leaves_verified_recovery_persistence_to_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    engine = ReflectionEngine(memory_manager=manager)
    trace = _verified_recovery_trace()

    reflection = engine.reflect("Fix and verify the parser", trace)

    assert reflection.value_decision.accepted is True
    assert manager.memories[MemoryScope.PROJECT].entries == []

    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=manager.workspace,
        enable_reranker=False,
        reflection_engine=engine,
    )

    entry_id = pipeline.write("Fix and verify the parser", trace)

    assert entry_id is not None
    entries = manager.memories[MemoryScope.PROJECT].entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == entry_id
    assert entry.approval_status == "approved"
    assert entry.is_active is True
    assert entry.metadata["claim_identity"]["claim_id"]
    assert entry.metadata["claim_identity"]["semantic_key"]
    assert len(entry.metadata["structured_reflection"]["claims"]) == 1
