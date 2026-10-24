"""Repeated manifest reads are observations, never verification or user approval."""

from dataclasses import replace
from pathlib import Path

import pytest

from minicode.agent_reflection import ReflectionEngine
from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline


@pytest.fixture(autouse=True)
def isolated_deposition(monkeypatch):
    monkeypatch.delenv("MINI_CODE_MEMORY_REDIS_URL", raising=False)
    monkeypatch.delenv("MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES", raising=False)


def _manifest_trace():
    # The live failure's last session had exactly this successful manifest
    # read and no installation, import execution, or verification.
    return [
        {"event_id": "read-call", "type": "tool_call", "call_id": "read",
         "tool_name": "read_file", "input": {"path": "requirements.txt"}},
        {"event_id": "read-result", "type": "tool_result", "call_id": "read",
         "tool_name": "read_file", "status": "success", "is_error": False,
         "output_summary": "FILE: requirements.txt\nOFFSET: 0\nEND: 12\nTOTAL_CHARS: 12\nTRUNCATED: no\nhttpx>=0.27\n"},
        {"event_id": "finished", "type": "task_result", "status": "success"},
    ]


@pytest.mark.parametrize("mode", ["capture", "inject"])
@pytest.mark.parametrize("gate_accepts", [True, False])
def test_three_independent_manifest_writes_stay_pending_across_reload(
    tmp_path: Path, monkeypatch, mode, gate_accepts,
):
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_MODE", mode)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    trace = _manifest_trace()
    real_engine = ReflectionEngine()
    rejected = real_engine.reflect("inspect dependencies", trace)
    rejected.value_decision = replace(
        rejected.value_decision, accepted=False, accepted_claim_ids=[],
        durable_signals=[], reason_codes=["no_durable_signal"],
    )

    class RejectedGate:
        def reflect(self, *_args, **_kwargs):
            return rejected

    first_id = None
    first_expiry = None
    for observation in range(1, 5):
        manager = MemoryManager(project_root=workspace, data_root=data_root)
        pipeline = MemoryPipeline(manager)
        pipeline.initialize(
            workspace_path=str(workspace),
            reflection_engine=real_engine if gate_accepts else RejectedGate(),
        )
        pipeline.write_result("inspect dependencies", trace)
        entries = manager.memories[MemoryScope.PROJECT].entries
        assert len(entries) == 1
        entry = entries[0]
        first_id = first_id or entry.id
        assert entry.id == first_id
        assert entry.metadata.get("recurrence", 1) == observation
        assert entry.approval_status == "pending"
        assert not entry.is_active
        assert "Declared in manifest; not yet exercised in this trace." in entry.content
        assert entry.metadata["structured_reflection"]["claims"][0]["epistemic_status"] == "inferred"
        assert "declared_dependency" in entry.metadata["deposition_draft"]["reason_codes"]
        if observation == 1:
            first_expiry = entry.metadata["deposition_draft"]["expires_at"]
        assert entry.metadata["deposition_draft"]["expires_at"] == first_expiry
        assert not any(
            row["action"] == "recurrence_promoted"
            for row in manager.get_approval_audit(entry.id)
        )


@pytest.mark.parametrize("mode", ["strict", "capture", "inject"])
@pytest.mark.parametrize("metadata", [
    {"deposition_draft": {"reason_codes": ["declared_dependency"]}},
    {"structured_reflection": {"claims": [{
        "claim_type": "dependency", "epistemic_status": "inferred",
        "limitations": ["Declared in manifest; not yet exercised in this trace."],
    }]}},
])
def test_direct_declaration_recurrence_never_substitutes_for_review(
    tmp_path: Path, monkeypatch, mode, metadata,
):
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_MODE", mode)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data")
    entry = manager.add_entry(
        MemoryScope.PROJECT, "dependency", "Project declared dependencies: httpx.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED, metadata=metadata,
    )
    assert entry is not None
    for _ in range(5):
        assert manager.reinforce_reflection_entry(entry.id)
    current = manager.memories[MemoryScope.PROJECT].entries[0]
    assert current.approval_status == "pending"
    assert current.metadata["recurrence"] == 6
    assert not any(row["action"] == "recurrence_promoted" for row in manager.get_approval_audit(entry.id))


def test_user_can_approve_repeated_declaration_and_recurrence_preserves_approval(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data")
    entry = manager.add_entry(
        MemoryScope.PROJECT, "dependency", "Project declared dependencies: httpx.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
        metadata={"deposition_draft": {"reason_codes": ["declared_dependency"]}},
    )
    assert entry is not None
    for _ in range(3):
        manager.reinforce_reflection_entry(entry.id)
    assert manager.memories[MemoryScope.PROJECT].entries[0].approval_status == "pending"
    manager.approve_entry(entry.id, actor="user", reason="Reviewed manifest declaration")
    assert manager.memories[MemoryScope.PROJECT].entries[0].approval_status == "approved"
    manager.reinforce_reflection_entry(entry.id)
    restored = MemoryManager(project_root=workspace, data_root=tmp_path / "data")
    current = restored.memories[MemoryScope.PROJECT].entries[0]
    assert current.approval_status == "approved"
    assert current.approval_actor == "user"


@pytest.mark.parametrize("metadata", [
    {},
    {"structured_reflection": {"claims": [{
        "claim_type": "dependency", "epistemic_status": "inferred",
        "limitations": ["Dependency use inferred from successful installation."],
    }]}},
    {"structured_reflection": {"claims": [{
        "claim_type": "dependency", "epistemic_status": "confirmed", "limitations": [],
    }]}},
])
def test_strict_ordinary_lesson_keeps_historical_recurrence_promotion(tmp_path: Path, monkeypatch, metadata):
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_MODE", "strict")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data")
    entry = manager.add_entry(
        MemoryScope.PROJECT, "lesson", "Run project checks from the repository root.",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
        metadata=metadata,
    )
    assert entry is not None
    for _ in range(2):
        manager.reinforce_reflection_entry(entry.id)
    assert manager.memories[MemoryScope.PROJECT].entries[0].approval_status == "approved"
