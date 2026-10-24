"""Retrying one rejected declaration must replay its existing pending draft."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from minicode.agent_reflection import ReflectionResult
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_pipeline import MemoryPipeline
from minicode.reflection_evidence import TaskEvidence
from minicode.reflection_synthesis import (
    DECLARED_DEPENDENCY_LIMITATION,
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionValueDecision,
)


class _RejectedDeclaration:
    """Return the same already-validated claim without invoking any model."""

    def __init__(self) -> None:
        claim = ReflectionClaim(
            claim_id="claim-declared-retry",
            claim_type="dependency",
            semantic_key="declared_dependencies_httpx",
            statement="Project declared dependencies: httpx.",
            evidence_ids=["manifest-read"],
            epistemic_status="inferred",
            limitations=[DECLARED_DEPENDENCY_LIMITATION],
        )
        self.result = ReflectionResult(
            task_summary="inspect manifest declaration",
            success=True,
            key_decisions=[], errors_encountered=[], lessons_learned=[],
            suggested_improvements=[], confidence=0.5,
            task_evidence=TaskEvidence(outcome="success"),
            reflection_candidate=ReflectionCandidate(
                task_summary="inspect manifest declaration", outcome="success",
                claims=[claim],
            ),
            claim_validation=ClaimValidationResult(valid_claims=[claim]),
            value_decision=ReflectionValueDecision(
                accepted=False, reason_codes=["no_durable_signal"],
                durable_signals=[], accepted_claim_ids=[],
                rejected_claim_ids=[claim.claim_id],
            ),
            structured_claims=[claim],
        )

    def reflect(self, *_args, **_kwargs) -> ReflectionResult:
        return self.result


@pytest.mark.parametrize("mode", ["capture", "inject"])
def test_rejected_declaration_same_operation_replays_without_extending_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_MODE", mode)
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES", "1")
    monkeypatch.delenv("MINI_CODE_MEMORY_REDIS_URL", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data")
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace), reflection_engine=_RejectedDeclaration(),
    )
    original_commit = manager.commit_reflection_batch
    commits = []

    def record_successful_commit(**kwargs):
        commit = original_commit(**kwargs)
        commits.append(commit)
        return commit

    monkeypatch.setattr(manager, "commit_reflection_batch", record_successful_commit)
    operation_id = "memoryop_" + "d" * 32
    now = 1_800_000_000.0
    monkeypatch.setattr("minicode.memory_pipeline.time.time", lambda: now)
    first = pipeline.write_result("inspect dependencies", [], operation_id=operation_id)
    original = manager.memories[MemoryScope.PROJECT].entries[0].to_dict()
    now += 30.0
    second = pipeline.write_result("inspect dependencies", [], operation_id=operation_id)

    assert first.reason_codes == second.reason_codes == ("reflection_not_persistable",)
    assert len(commits) == 2, "The retry must replay its draft batch, not swallow a changed-TTL conflict"
    assert not commits[0].replayed
    assert commits[1].replayed
    entries = manager.memories[MemoryScope.PROJECT].entries
    assert len(entries) == 1
    assert entries[0].to_dict() == original
    assert entries[0].approval_status == "pending"
    assert entries[0].metadata.get("recurrence", 1) == 1


@pytest.mark.parametrize("gate_accepts", [True, False])
def test_retry_of_later_declaration_recurrence_reuses_original_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate_accepts: bool,
) -> None:
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_MODE", "inject")
    monkeypatch.setenv("MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES", "1")
    monkeypatch.delenv("MINI_CODE_MEMORY_REDIS_URL", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = MemoryManager(project_root=workspace, data_root=tmp_path / "data")
    reflection = _RejectedDeclaration()
    if gate_accepts:
        reflection.result.value_decision = replace(
            reflection.result.value_decision, accepted=True,
            accepted_claim_ids=["claim-declared-retry"], rejected_claim_ids=[],
            durable_signals=["declared_dependency"],
            reason_codes=["accepted_durable_reflection"],
        )
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(workspace_path=str(workspace), reflection_engine=reflection)
    original_commit = manager.commit_reflection_batch
    commits = []

    def record_successful_commit(**kwargs):
        commit = original_commit(**kwargs)
        commits.append(commit)
        return commit

    monkeypatch.setattr(manager, "commit_reflection_batch", record_successful_commit)
    now = 1_800_000_000.0
    monkeypatch.setattr("minicode.memory_pipeline.time.time", lambda: now)
    pipeline.write_result("inspect dependencies", [], operation_id="memoryop_" + "a" * 32)
    created = manager.memories[MemoryScope.PROJECT].entries[0]
    original_expiry = created.metadata["deposition_draft"]["expires_at"]
    now += 30.0
    second_operation = "memoryop_" + "b" * 32
    pipeline.write_result("inspect dependencies", [], operation_id=second_operation)
    reinforced = manager.memories[MemoryScope.PROJECT].entries[0].to_dict()
    assert reinforced["metadata"]["recurrence"] == 2
    now += 30.0
    pipeline.write_result("inspect dependencies", [], operation_id=second_operation)

    assert len(commits) == 3, "Retry of a reinforced declaration must resolve its original TTL"
    assert commits[1].materializations[0].disposition == "reinforced"
    assert commits[2].replayed
    current = manager.memories[MemoryScope.PROJECT].entries[0]
    assert current.to_dict() == reinforced
    assert current.metadata["deposition_draft"]["expires_at"] == original_expiry
    assert current.approval_status == "pending"
