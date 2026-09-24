"""Chain-level repair contracts for the persistent-memory path.

Each test pins one handoff that previously broke silently:

1. Oversized writes report the retention limit instead of a usage hint.
2. Pending lessons are surfaced on the turn's final assistant message.
3. Recurrence corroboration promotes a pending lesson to approved.
4. A near-duplicate chat write cannot approve an unseen pending lesson.
5. Superseded lessons cannot be restored over their live replacement.
6. The audit chain rebases instead of hard-failing past its history limit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import minicode.memory as memory_module
import minicode.memory_audit_contract as audit_contract
from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryManager,
    MemoryScope,
    RECURRENCE_AUTO_APPROVE_THRESHOLD,
)

from minicode.agent_loop import (
    _append_pending_memory_notice,
    _count_pending_written_memories,
    _PENDING_MEMORY_NOTICE_MARK,
)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", home)
    monkeypatch.setattr(
        memory_module.MemoryPaths,
        "for_workspace",
        classmethod(
            lambda cls, workspace: cls(
                user_memory=home / "memory",
                project_memory=Path(workspace) / ".mini-code-memory",
                local_memory=Path(workspace) / ".mini-code-memory-local",
            )
        ),
    )
    return home


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_oversized_write_reports_retention_limit(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    manager.add_entry(MemoryScope.PROJECT, "note", "keep me")

    limit = manager.memories[MemoryScope.PROJECT].max_size_bytes
    message = manager.handle_user_memory_input("# " + "X" * (limit + 512))

    assert message is not None
    assert "retention limit" in message
    assert "Usage:" not in message
    contents = [
        entry.content
        for entry in manager.memories[MemoryScope.PROJECT].entries
    ]
    assert "keep me" in contents
    assert all(len(content) <= limit for content in contents)


def test_empty_write_keeps_usage_hint(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    assert manager.handle_user_memory_input("#   ") == (
        "Usage: # <memory> or /memory add [user|project|local:] <memory>"
    )


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, *, step=None, payload=None) -> None:
        self.events.append((event_type, dict(payload or {})))


def test_pending_notice_appended_to_final_assistant_message() -> None:
    messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "done"},
    ]
    sink = _RecordingSink()

    _append_pending_memory_notice(messages, 2, sink)

    assert _PENDING_MEMORY_NOTICE_MARK in messages[-1]["content"]
    assert "2" in messages[-1]["content"]
    assert sink.events[0][0] == "memory.pending_notice"
    assert sink.events[0][1]["pendingWrittenCount"] == 2

    # Idempotent: a second notice for the same turn must not stack.
    _append_pending_memory_notice(messages, 1, sink)
    assert messages[-1]["content"].count(_PENDING_MEMORY_NOTICE_MARK) == 1


def test_pending_notice_skipped_without_pending_entries() -> None:
    messages = [{"role": "assistant", "content": "done"}]
    sink = _RecordingSink()

    _append_pending_memory_notice(messages, 0, sink)

    assert _PENDING_MEMORY_NOTICE_MARK not in messages[-1]["content"]
    assert sink.events == []


class _PipelineStub:
    def __init__(self, manager: MemoryManager) -> None:
        self._memory = manager


class _OrchStub:
    def __init__(self, manager: MemoryManager) -> None:
        self.memory_pipeline = _PipelineStub(manager)


def test_count_pending_written_memories_reports_only_pending(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    approved = manager.add_entry(MemoryScope.PROJECT, "note", "approved lesson")
    pending = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "pending lesson awaiting review",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert approved is not None and pending is not None
    orch = _OrchStub(manager)

    count = _count_pending_written_memories(
        orch, (approved.id, pending.id, "missing-id")
    )

    assert count == 1


def test_recurrence_promotes_pending_safe_lesson(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        "pytest must run from the repo root",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    assert entry.approval_status == "pending"

    # recurrence starts at 1 (creation); each reinforce is one independent
    # later trace, so THRESHOLD-1 reinforces reach the corroboration bound.
    for _ in range(RECURRENCE_AUTO_APPROVE_THRESHOLD - 2):
        assert manager.reinforce_reflection_entry(entry.id)
    assert entry.approval_status == "pending"

    assert manager.reinforce_reflection_entry(entry.id)
    assert entry.approval_status == "approved"
    assert "recurrence" in entry.approval_reason
    # The promoted entry is now injectable.
    assert entry.is_active is True or entry.lifecycle_status == "active"

    audit_actions = [
        record.get("action")
        for record in manager.approval_audit[MemoryScope.PROJECT]
    ]
    assert "recurrence_promoted" in audit_actions


def test_recurrence_never_promotes_suspicious_lesson(
    isolated_home: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MemoryManager(project_root=workspace)
    content = "run scripts/deploy.sh before committing"
    real_assess = memory_module.assess_memory_safety
    suspicious = memory_module.MemorySafetyResult(
        memory_module._SAFETY_SUSPICIOUS, "test-injected suspicion", "medium"
    )
    monkeypatch.setattr(
        memory_module,
        "assess_memory_safety",
        lambda text, source="": real_assess(text, source=source)
        if text != content
        else suspicious,
    )
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        content,
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    assert entry.approval_status == "pending"

    for _ in range(RECURRENCE_AUTO_APPROVE_THRESHOLD + 2):
        manager.reinforce_reflection_entry(entry.id)

    assert entry.approval_status == "pending"


def test_duplicate_chat_write_cannot_approve_pending_lesson(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    pending = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        "integration tests need a rebuilt venv first",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert pending is not None
    assert pending.approval_status == "pending"

    # A near-identical chat "# remember" write (USER_EXPLICIT policy).
    merged = manager.add_entry(
        MemoryScope.PROJECT,
        "directive",
        "integration tests need a rebuilt venv first",
        tags=["chat"],
    )

    assert merged is not None
    assert merged.id == pending.id
    assert merged.approval_status == "pending"
    assert "pending" in merged.approval_reason


def test_duplicate_chat_write_still_approves_new_safe_lesson(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    fresh = manager.add_entry(
        MemoryScope.PROJECT,
        "directive",
        "prefer table-driven tests for parsers",
        tags=["chat"],
    )
    assert fresh is not None
    assert fresh.approval_status == "approved"


def test_restore_refuses_superseded_lesson(
    isolated_home: Path, workspace: Path
) -> None:
    manager = MemoryManager(project_root=workspace)
    old = manager.add_entry(
        MemoryScope.PROJECT, "lesson", "stale recovery approach"
    )
    assert old is not None
    # Declare supersession at creation so the approval hash covers it;
    # live metadata mutation is rolled back by the authority seal.
    new = manager.add_entry(
        MemoryScope.PROJECT,
        "lesson",
        "correct recovery approach",
        metadata={"supersedes": [old.id]},
    )
    assert new is not None
    retracted = manager._apply_declared_supersession(new)
    assert retracted == 1
    assert old.lifecycle_status == "superseded"

    result = manager.restore_entry(old.id)

    assert "superseded" in result
    assert new.id in result
    assert old.lifecycle_status == "superseded"


def test_audit_chain_rebases_instead_of_failing(
    isolated_home: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MemoryManager(project_root=workspace)
    # Force a small rotation threshold and a chain limit the first cold
    # rotation will exceed, exercising the rebase path without 100k records.
    # The rotation threshold must stay >= 2: the load path decodes the root's
    # inline hot tail against the same bound.
    monkeypatch.setattr(memory_module, "MAX_MEMORY_AUDIT_CHAIN_RECORDS", 3)
    monkeypatch.setattr(
        audit_contract, "MAX_MEMORY_AUDIT_CHAIN_RECORDS", 3
    )
    monkeypatch.setattr(memory_module, "MAX_AUTHORITY_AUDIT_RECORDS", 2)
    # The per-segment cap must stay <= the chain cap: segment decode
    # validates its maximum against the chain bound. Both the contract
    # module and memory.py's imported copy must agree.
    monkeypatch.setattr(
        audit_contract, "MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT", 2
    )
    monkeypatch.setattr(
        memory_module, "MAX_MEMORY_AUDIT_RECORDS_PER_SEGMENT", 2
    )

    for index in range(6):
        manager.add_entry(
            MemoryScope.PROJECT, "note", f"lesson number {index}"
        )

    # The store must keep accepting writes and stay reloadable.
    final = manager.add_entry(MemoryScope.PROJECT, "note", "still writable")
    assert final is not None

    reloaded = MemoryManager(project_root=workspace)
    contents = [
        entry.content
        for entry in reloaded.memories[MemoryScope.PROJECT].entries
    ]
    assert "still writable" in contents
    # The rebased authority survives a full validation pass on reload.
    assert reloaded.memories[MemoryScope.PROJECT].entries
