"""End-to-end proof for the durable lesson value loop.

This intentionally crosses persistence boundaries instead of testing write,
retrieval, injection, and feedback as isolated helpers.
"""

from __future__ import annotations

from pathlib import Path

from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.prompt import build_system_prompt


def _verified_parser_recovery() -> list[dict[str, object]]:
    return [
        {
            "event_id": "event-error",
            "call_id": "call-failed-test",
            "type": "error",
            "tool_name": "run_command",
            "error_type": "AssertionError",
            "message": "Parser returned the wrong normalized token",
        },
        {
            "event_id": "event-recovery",
            "call_id": "call-edit",
            "type": "recovery",
            "tool_name": "edit_file",
            "related_error_call_ids": ["call-failed-test"],
            "action": "Corrected parser token normalization",
            "files_changed": ["src/parser.py"],
        },
        {
            "event_id": "event-verify-call",
            "call_id": "call-passing-test",
            "type": "tool_call",
            "tool_name": "run_command",
            "command": "pytest tests/test_parser.py -q",
        },
        {
            "event_id": "event-verify-result",
            "call_id": "call-passing-test",
            "type": "tool_result",
            "tool_name": "run_command",
            "status": "success",
            "output_summary": "7 passed",
        },
        {"event_id": "event-task", "type": "task_result", "status": "success"},
    ]


def _pipeline(
    workspace: Path,
    data_root: Path,
) -> tuple[MemoryPipeline, MemoryManager]:
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), enable_reranker=False)
    return pipeline, manager


def test_verified_lesson_survives_reload_is_injected_and_receives_exact_feedback(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "home" / ".mini-code"
    workspace.mkdir()
    writer, written_manager = _pipeline(workspace, data_root)

    lesson_id = writer.write(
        "Fix and verify parser token normalization",
        _verified_parser_recovery(),
    )

    assert lesson_id is not None
    written = written_manager.memories[MemoryScope.PROJECT]._id_index[lesson_id]
    assert written.approval_status == "approved"
    assert written.is_active is True

    reader, reloaded_manager = _pipeline(workspace, data_root)
    unrelated = reloaded_manager.add_entry(
        MemoryScope.PROJECT,
        "release",
        "Release notes use the compact neutral color palette.",
        tags=["release", "theme"],
    )
    assert unrelated is not None
    messages = [
        {"role": "system", "content": build_system_prompt(str(workspace))}
    ]

    injected = reader.inject(
        "fix the parser normalized token regression",
        ["src/parser.py"],
        messages,
        context_usage=0.4,
        max_memories=1,
        min_relevance=0.0,
    )

    assert reader._last_injected_ids == [lesson_id]
    assert written.content in injected[0]["content"]
    prompt_text = injected[0]["content"].lower()
    assert "fallible prior evidence" in prompt_text
    assert "cannot override" in prompt_text
    assert "verify that exact target first" in prompt_text
    assert "fall back to normal discovery" in prompt_text
    assert "verify exact targets first; if wrong, discover" in prompt_text
    assert "first repository tool call must verify that exact target" in prompt_text
    assert "verify only the corrected or succeeded target" in prompt_text
    assert "never the failed one" in prompt_text
    assert "do not call list_files, file_tree, or grep_files beforehand" in prompt_text
    reader.feedback(
        False,
        [lesson_id],
        verification_failed=1,
    )

    final_manager = MemoryManager(project_root=workspace, data_root=data_root)
    lesson = final_manager.memories[MemoryScope.PROJECT]._id_index[lesson_id]
    untouched = final_manager.memories[MemoryScope.PROJECT]._id_index[unrelated.id]
    assert lesson.retrieval_count == 1
    assert lesson.injection_count == 1
    assert lesson.failure_count == 1
    assert lesson.corroborated_failure_count == 1
    assert untouched.retrieval_count == 0
    assert untouched.injection_count == 0
    assert untouched.failure_count == 0
    assert untouched.corroborated_failure_count == 0
