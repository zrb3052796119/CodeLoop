from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from minicode.agent_reflection import ReflectionResult
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_blob_store import MemoryBlobStore
from minicode.memory_pipeline import MemoryPipeline
from minicode.memory_store import MemoryStoreConflict
from minicode.memory_write_contract import (
    MemoryAuthorityCommit,
    MemoryAuthorityReceiptReference,
)
from minicode.project_facts import ProjectFactsStore
from minicode.reflection_evidence import LibraryEvidence, TaskEvidence
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionValueDecision,
)
from minicode.turn_cancellation import TurnCancellationRequested


class _ReflectionStub:
    def __init__(
        self,
        result: ReflectionResult,
        *,
        shadow_error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.shadow_error = shadow_error

    def reflect(self, *_args, **_kwargs) -> ReflectionResult:
        return self.result

    def complete_shadow(self, *_args, **_kwargs) -> None:
        if self.shadow_error is not None:
            raise self.shadow_error


def _operation(index: int) -> str:
    return f"memoryop_{index:032x}"


def _fact_path(workspace: Path) -> Path:
    return workspace / ".mini-code-memory" / "project_facts.json"


def _memory_path(workspace: Path) -> Path:
    return workspace / ".mini-code-memory" / "memory.json"


def _remove_memory_operation_authority(workspace: Path) -> None:
    """Model a pre-envelope facts-only write with no Memory side effect."""
    _memory_path(workspace).unlink()
    (workspace / ".mini-code-memory" / "MEMORY.md").unlink(missing_ok=True)


def _downgrade_lesson_receipt_to_legacy_exact(
    workspace: Path,
    *,
    exact_digest: str,
) -> None:
    authority_path = _memory_path(workspace)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    reference = MemoryAuthorityReceiptReference.from_dict(
        authority["write_receipts"][0]
    )
    raw = MemoryBlobStore(authority_path.parent).read_json(reference.blob)
    legacy_receipt = MemoryAuthorityCommit.from_dict(raw).to_dict()
    legacy_receipt["payloadDigest"] = exact_digest
    legacy_commit = MemoryAuthorityCommit.from_dict(legacy_receipt)
    blob = MemoryBlobStore(authority_path.parent).put_json(
        legacy_commit.to_dict()
    )
    authority["write_receipts"] = [
        MemoryAuthorityReceiptReference.from_commit(
            legacy_commit,
            blob,
        ).to_dict()
    ]
    authority["pipeline_operation_receipts"] = []
    authority_path.write_text(json.dumps(authority), encoding="utf-8")


def _reflection_result(*, include_lesson: bool = True) -> ReflectionResult:
    dependency = ReflectionClaim(
        claim_id="claim-dependency",
        claim_type="dependency",
        semantic_key="dependency_httpx",
        statement="The manifest confirms dependency httpx.",
        evidence_ids=["event-dependency"],
        epistemic_status="confirmed",
        verification_ids=["verification-dependency"],
    )
    lesson = ReflectionClaim(
        claim_id="claim-lesson",
        claim_type="approach",
        semantic_key="verified_retry_boundary",
        statement="Keep the verified retry boundary around the client call.",
        evidence_ids=["event-lesson"],
        epistemic_status="confirmed",
        verification_ids=["verification-lesson"],
    )
    claims = [dependency, lesson] if include_lesson else [dependency]
    return ReflectionResult(
        task_summary="persist a lesson and one deterministic dependency",
        success=True,
        key_decisions=[],
        errors_encountered=[],
        lessons_learned=[],
        suggested_improvements=[],
        confidence=0.95,
        task_evidence=TaskEvidence(
            libraries=[
                LibraryEvidence(
                    name="httpx",
                    status="confirmed",
                    event_ids=("event-dependency",),
                )
            ],
            outcome="success",
        ),
        reflection_candidate=ReflectionCandidate(
            task_summary="persist a lesson and one deterministic dependency",
            outcome="success",
            claims=claims,
        ),
        claim_validation=ClaimValidationResult(valid_claims=claims),
        value_decision=ReflectionValueDecision(
            accepted=True,
            reason_codes=["accepted_durable_reflection"],
            durable_signals=["verified_approach"],
            accepted_claim_ids=[claim.claim_id for claim in claims],
        ),
        structured_claims=claims,
    )


def _lesson_only_reflection_result() -> ReflectionResult:
    result = _reflection_result()
    lesson = next(
        claim
        for claim in result.structured_claims
        if claim.claim_type == "approach"
    )
    return replace(
        result,
        task_evidence=TaskEvidence(outcome="success"),
        reflection_candidate=replace(
            result.reflection_candidate,
            claims=[lesson],
        ),
        claim_validation=ClaimValidationResult(valid_claims=[lesson]),
        value_decision=replace(
            result.value_decision,
            accepted_claim_ids=[lesson.claim_id],
        ),
        structured_claims=[lesson],
    )


def _same_lesson_without_accepted_dependency(
    result: ReflectionResult,
) -> ReflectionResult:
    """Drop only the facts effect while preserving lesson authority bytes."""
    lesson = next(
        claim
        for claim in result.structured_claims
        if claim.claim_type == "approach"
    )
    return replace(
        result,
        value_decision=replace(
            result.value_decision,
            accepted_claim_ids=[lesson.claim_id],
        ),
    )


def _pipeline(
    workspace: Path,
    data_root: Path,
    result: ReflectionResult,
    *,
    shadow_error: BaseException | None = None,
) -> MemoryPipeline:
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        reflection_engine=_ReflectionStub(result, shadow_error=shadow_error),
    )
    return pipeline


def test_same_project_facts_operation_replays_without_rewriting_or_recounting(
    tmp_path: Path,
) -> None:
    provenance = {
        "source": "reflection_evidence",
        "run_id": "run-facts-replay",
        "event_ids": ["event-dependency"],
    }
    operation_id = _operation(1)
    first_store = ProjectFactsStore(tmp_path)

    first = first_store.observe_dependencies_once(
        ["httpx"],
        operation_id=operation_id,
        provenance=provenance,
    )
    before = _fact_path(tmp_path).read_bytes()
    occurrences_before = first_store.snapshot()["dependency:httpx"].occurrences

    fresh_store = ProjectFactsStore(tmp_path)
    replay = fresh_store.observe_dependencies_once(
        ["httpx"],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert first.status == "committed"
    assert first.added_count == 1
    assert first.updated_count == 0
    assert replay.status == "replayed"
    assert replay.added_count == 0
    assert replay.updated_count == 0
    assert _fact_path(tmp_path).read_bytes() == before
    assert (
        fresh_store.snapshot()["dependency:httpx"].occurrences
        == occurrences_before
        == 1
    )


def test_reused_project_facts_operation_with_changed_dependencies_fails_closed(
    tmp_path: Path,
) -> None:
    provenance = {
        "source": "reflection_evidence",
        "run_id": "run-facts-conflict",
    }
    operation_id = _operation(2)
    store = ProjectFactsStore(tmp_path)
    committed = store.observe_dependencies_once(
        ["httpx"],
        operation_id=operation_id,
        provenance=provenance,
    )
    before = _fact_path(tmp_path).read_bytes()

    with pytest.raises(
        MemoryStoreConflict,
        match="Project Facts operation payload changed",
    ):
        ProjectFactsStore(tmp_path).observe_dependencies_once(
            ["tenacity"],
            operation_id=operation_id,
            provenance=provenance,
        )

    assert committed.status == "committed"
    assert _fact_path(tmp_path).read_bytes() == before
    facts = ProjectFactsStore(tmp_path).snapshot()
    assert set(facts) == {"dependency:httpx"}
    assert facts["dependency:httpx"].occurrences == 1


def test_pipeline_lesson_replay_also_replays_project_facts_without_recounting(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    reflection = _reflection_result()
    operation_id = _operation(3)
    provenance = {"run_id": "run-pipeline-replay", "turn_id": "turn-1"}

    first_pipeline = _pipeline(workspace, data_root, reflection)
    first = first_pipeline.write_result(
        "persist the verified client lesson",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    facts_before = _fact_path(workspace).read_bytes()
    occurrences_before = (
        ProjectFactsStore(workspace).snapshot()["dependency:httpx"].occurrences
    )

    fresh_pipeline = _pipeline(workspace, data_root, reflection)
    replay = fresh_pipeline.write_result(
        "persist the verified client lesson",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert first.authority_commit is not None
    assert first.authority_commit.replayed is False
    assert first.project_facts_effect.status == "committed"
    assert replay.authority_commit == first.authority_commit.as_replay()
    assert replay.project_facts_effect.status == "replayed"
    assert _fact_path(workspace).read_bytes() == facts_before
    assert (
        ProjectFactsStore(workspace).snapshot()["dependency:httpx"].occurrences
        == occurrences_before
        == 1
    )


def test_facts_only_commit_survives_shadow_cancellation_as_structured_result(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pipeline = _pipeline(
        workspace,
        tmp_path / "home" / ".mini-code",
        _reflection_result(include_lesson=False),
        shadow_error=TurnCancellationRequested("cancel after facts commit"),
    )

    result = pipeline.write_result(
        "persist the confirmed dependency",
        [],
        operation_id=_operation(4),
        provenance={"run_id": "run-facts-cancel", "turn_id": "turn-1"},
    )

    assert result.authority_commit is None
    assert result.authority_kind == "none"
    assert result.project_facts_effect.status == "committed"
    assert result.project_facts_effect.added_count == 1
    assert result.post_commit_interrupted is True
    assert "post_commit_interrupted" in result.reason_codes
    assert ProjectFactsStore(workspace).snapshot()["dependency:httpx"].occurrences == 1


def test_project_facts_capacity_rejects_batch_without_false_commit(
    tmp_path: Path,
) -> None:
    store = ProjectFactsStore(tmp_path)
    initial = store.observe_dependencies_once(
        [f"pkg{index:03d}" for index in range(200)],
        operation_id=_operation(5),
        provenance={"run_id": "run-fill-capacity"},
    )
    before = _fact_path(tmp_path).read_bytes()

    with pytest.raises(MemoryStoreConflict, match="capacity exceeded"):
        store.observe_dependencies_once(
            ["zzzz"],
            operation_id=_operation(6),
            provenance={"run_id": "run-over-capacity"},
        )

    assert initial.status == "committed"
    assert initial.added_count == 200
    assert len(store.snapshot()) == 200
    assert "dependency:zzzz" not in store.snapshot()
    assert _fact_path(tmp_path).read_bytes() == before


def test_project_facts_write_fails_closed_on_corrupt_authority(
    tmp_path: Path,
) -> None:
    operation_id = _operation(7)
    provenance = {"run_id": "run-corrupt-facts"}
    store = ProjectFactsStore(tmp_path)
    store.observe_dependencies_once(
        ["httpx"],
        operation_id=operation_id,
        provenance=provenance,
    )
    corrupt_bytes = b'{"facts":['
    _fact_path(tmp_path).write_bytes(corrupt_bytes)

    with pytest.raises(MemoryStoreConflict, match="authority is unreadable"):
        ProjectFactsStore(tmp_path).observe_dependencies_once(
            ["httpx"],
            operation_id=operation_id,
            provenance=provenance,
        )

    assert _fact_path(tmp_path).read_bytes() == corrupt_bytes


def test_facts_only_envelope_blocks_later_lesson_pipeline_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(8)
    provenance = {"run_id": "run-kind-change", "turn_id": "turn-1"}

    first_pipeline = _pipeline(
        workspace,
        data_root,
        _reflection_result(include_lesson=False),
    )
    first = first_pipeline.write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    memory_authority = workspace / ".mini-code-memory" / "memory.json"
    memory_before = memory_authority.read_bytes()
    facts_before = _fact_path(workspace).read_bytes()

    fresh = _pipeline(
        workspace,
        data_root,
        _lesson_only_reflection_result(),
    )
    conflict = fresh.write_result(
        "persist the verified client lesson",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert first.project_facts_effect.status == "committed"
    assert conflict.authority_commit is None
    assert "memory_write_conflict" in conflict.reason_codes
    assert memory_authority.read_bytes() == memory_before
    assert _fact_path(workspace).read_bytes() == facts_before
    assert fresh._memory.memories[MemoryScope.PROJECT].entries == []


def test_lesson_envelope_blocks_later_facts_only_pipeline_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(9)
    provenance = {"run_id": "run-kind-change", "turn_id": "turn-2"}

    first_pipeline = _pipeline(
        workspace,
        data_root,
        _lesson_only_reflection_result(),
    )
    first = first_pipeline.write_result(
        "persist the verified client lesson",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    memory_authority = workspace / ".mini-code-memory" / "memory.json"
    memory_before = memory_authority.read_bytes()

    fresh = _pipeline(
        workspace,
        data_root,
        _reflection_result(include_lesson=False),
    )
    conflict = fresh.write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert first.authority_commit is not None
    assert conflict.authority_commit is None
    assert "memory_write_conflict" in conflict.reason_codes
    assert memory_authority.read_bytes() == memory_before
    assert _fact_path(workspace).exists() is False
    assert len(fresh._memory.memories[MemoryScope.PROJECT].entries) == 1


def test_mixed_envelope_blocks_retry_that_drops_project_facts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(10)
    provenance = {"run_id": "run-kind-change", "turn_id": "turn-3"}

    reflection = _reflection_result()
    first_pipeline = _pipeline(workspace, data_root, reflection)
    first = first_pipeline.write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    memory_authority = workspace / ".mini-code-memory" / "memory.json"
    memory_before = memory_authority.read_bytes()
    facts_before = _fact_path(workspace).read_bytes()

    fresh = _pipeline(
        workspace,
        data_root,
        _same_lesson_without_accepted_dependency(reflection),
    )
    conflict = fresh.write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert first.authority_commit is not None
    assert first.project_facts_effect.status == "committed"
    assert conflict.authority_commit is None
    assert "memory_write_conflict" in conflict.reason_codes
    assert memory_authority.read_bytes() == memory_before
    assert _fact_path(workspace).read_bytes() == facts_before


@pytest.mark.parametrize("include_lesson", [False, True])
def test_project_facts_failure_resumes_without_rewriting_memory_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_lesson: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(11 if include_lesson else 12)
    provenance = {
        "run_id": "run-facts-resume",
        "turn_id": "turn-with-lesson" if include_lesson else "turn-facts-only",
    }
    reflection = _reflection_result(include_lesson=include_lesson)
    first_pipeline = _pipeline(workspace, data_root, reflection)

    def fail_once(*_args, **_kwargs):
        raise OSError("controlled failure after Memory operation binding")

    monkeypatch.setattr(
        first_pipeline._project_facts,
        "observe_dependencies_once",
        fail_once,
    )
    first = first_pipeline.write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    memory_authority = workspace / ".mini-code-memory" / "memory.json"
    memory_before = memory_authority.read_bytes()

    fresh = _pipeline(workspace, data_root, reflection)
    recovered = fresh.write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert first.project_facts_effect.status == "failed"
    assert _fact_path(workspace).exists()
    assert recovered.project_facts_effect.status == "committed"
    assert recovered.project_facts_effect.added_count == 1
    assert memory_authority.read_bytes() == memory_before
    if include_lesson:
        assert recovered.authority_commit is not None
        assert recovered.authority_commit.replayed is True
    else:
        assert recovered.authority_commit is None
    assert ProjectFactsStore(workspace).snapshot()[
        "dependency:httpx"
    ].occurrences == 1


def test_legacy_facts_only_replay_adds_envelope_without_rewriting_facts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(20)
    provenance = {"run_id": "run-legacy-facts", "turn_id": "turn-1"}
    reflection = _reflection_result(include_lesson=False)

    first = _pipeline(workspace, data_root, reflection).write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    _remove_memory_operation_authority(workspace)
    facts_before = _fact_path(workspace).read_bytes()
    occurrences_before = ProjectFactsStore(workspace).snapshot()[
        "dependency:httpx"
    ].occurrences

    replay = _pipeline(workspace, data_root, reflection).write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert replay.payload_digest == first.payload_digest
    assert replay.project_facts_effect.status == "replayed"
    assert _fact_path(workspace).read_bytes() == facts_before
    assert ProjectFactsStore(workspace).snapshot()[
        "dependency:httpx"
    ].occurrences == occurrences_before
    authority = json.loads(_memory_path(workspace).read_text(encoding="utf-8"))
    assert authority["write_receipts"] == []
    assert authority["pipeline_operation_receipts"] == [
        {
            "operationId": operation_id,
            "aggregateDigest": first.payload_digest,
        }
    ]


@pytest.mark.parametrize(
    "retry_reflection",
    [_lesson_only_reflection_result(), _reflection_result()],
    ids=["lesson", "mixed"],
)
def test_legacy_facts_only_cannot_change_output_kind(
    tmp_path: Path,
    retry_reflection: ReflectionResult,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(21)
    provenance = {"run_id": "run-legacy-kind", "turn_id": "turn-1"}

    first = _pipeline(
        workspace,
        data_root,
        _reflection_result(include_lesson=False),
    ).write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    assert first.project_facts_effect.status == "committed"
    _remove_memory_operation_authority(workspace)
    facts_before = _fact_path(workspace).read_bytes()

    conflict = _pipeline(
        workspace,
        data_root,
        retry_reflection,
    ).write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert "memory_write_conflict" in conflict.reason_codes
    assert conflict.authority_commit is None
    assert _memory_path(workspace).exists() is False
    assert _fact_path(workspace).read_bytes() == facts_before


def test_legacy_mixed_replay_upgrades_both_exact_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(22)
    provenance = {"run_id": "run-legacy-mixed", "turn_id": "turn-1"}
    reflection = _reflection_result()
    first_pipeline = _pipeline(workspace, data_root, reflection)
    original_commit = first_pipeline._memory.commit_reflection_batch
    captured: dict[str, object] = {}

    def capture_commit(**kwargs):
        captured.update(kwargs)
        return original_commit(**kwargs)

    monkeypatch.setattr(
        first_pipeline._memory,
        "commit_reflection_batch",
        capture_commit,
    )
    first = first_pipeline.write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    exact_digest = captured["authority_payload_digest"]
    assert isinstance(exact_digest, str)
    _downgrade_lesson_receipt_to_legacy_exact(
        workspace,
        exact_digest=exact_digest,
    )
    facts_before = _fact_path(workspace).read_bytes()
    occurrences_before = ProjectFactsStore(workspace).snapshot()[
        "dependency:httpx"
    ].occurrences

    replay = _pipeline(workspace, data_root, reflection).write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert replay.authority_commit is not None
    assert replay.authority_commit.replayed is True
    assert replay.authority_commit.payload_digest == first.payload_digest
    assert replay.project_facts_effect.status == "replayed"
    assert _fact_path(workspace).read_bytes() == facts_before
    assert ProjectFactsStore(workspace).snapshot()[
        "dependency:httpx"
    ].occurrences == occurrences_before
    authority = json.loads(_memory_path(workspace).read_text(encoding="utf-8"))
    assert authority["write_receipts"][0]["payloadDigest"] == first.payload_digest
    assert authority["pipeline_operation_receipts"] == [
        {
            "operationId": operation_id,
            "aggregateDigest": first.payload_digest,
        }
    ]


def test_legacy_mixed_cannot_drop_project_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(23)
    provenance = {"run_id": "run-legacy-mixed", "turn_id": "turn-drop"}
    reflection = _reflection_result()
    first_pipeline = _pipeline(workspace, data_root, reflection)
    original_commit = first_pipeline._memory.commit_reflection_batch
    captured: dict[str, object] = {}

    def capture_commit(**kwargs):
        captured.update(kwargs)
        return original_commit(**kwargs)

    monkeypatch.setattr(
        first_pipeline._memory,
        "commit_reflection_batch",
        capture_commit,
    )
    first_pipeline.write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    exact_digest = captured["authority_payload_digest"]
    assert isinstance(exact_digest, str)
    _downgrade_lesson_receipt_to_legacy_exact(
        workspace,
        exact_digest=exact_digest,
    )
    memory_before = _memory_path(workspace).read_bytes()
    facts_before = _fact_path(workspace).read_bytes()

    conflict = _pipeline(
        workspace,
        data_root,
        _same_lesson_without_accepted_dependency(reflection),
    ).write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert "memory_write_conflict" in conflict.reason_codes
    assert conflict.authority_commit is None
    assert _memory_path(workspace).read_bytes() == memory_before
    assert _fact_path(workspace).read_bytes() == facts_before


def test_legacy_mixed_missing_facts_receipt_fails_before_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(24)
    provenance = {"run_id": "run-partial-legacy", "turn_id": "turn-1"}
    reflection = _reflection_result()
    first_pipeline = _pipeline(workspace, data_root, reflection)
    original_commit = first_pipeline._memory.commit_reflection_batch
    captured: dict[str, object] = {}

    def capture_commit(**kwargs):
        captured.update(kwargs)
        return original_commit(**kwargs)

    monkeypatch.setattr(
        first_pipeline._memory,
        "commit_reflection_batch",
        capture_commit,
    )
    first_pipeline.write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    exact_digest = captured["authority_payload_digest"]
    assert isinstance(exact_digest, str)
    _downgrade_lesson_receipt_to_legacy_exact(
        workspace,
        exact_digest=exact_digest,
    )
    facts_authority = json.loads(_fact_path(workspace).read_text(encoding="utf-8"))
    facts_authority["write_receipts"] = []
    _fact_path(workspace).write_text(
        json.dumps(facts_authority),
        encoding="utf-8",
    )
    memory_before = _memory_path(workspace).read_bytes()
    facts_before = _fact_path(workspace).read_bytes()

    conflict = _pipeline(workspace, data_root, reflection).write_result(
        "persist lesson and dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert "memory_write_conflict" in conflict.reason_codes
    assert conflict.authority_commit is None
    assert _memory_path(workspace).read_bytes() == memory_before
    assert _fact_path(workspace).read_bytes() == facts_before


def test_legacy_reconciliation_holds_memory_then_facts_through_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "home" / ".mini-code"
    operation_id = _operation(25)
    provenance = {"run_id": "run-lock-order", "turn_id": "turn-1"}
    reflection = _reflection_result(include_lesson=False)

    _pipeline(workspace, data_root, reflection).write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )
    _remove_memory_operation_authority(workspace)
    fresh = _pipeline(workspace, data_root, reflection)
    original_guard = fresh._project_facts.operation_receipt_guard
    original_save = fresh._memory._save_scope_locked
    facts_guard_active = False
    observed_lock_order: list[bool] = []
    observed_commit_guard: list[bool] = []

    @contextmanager
    def guarded_receipt(current_operation_id: str):
        nonlocal facts_guard_active
        observed_lock_order.append(fresh._memory.in_write_transaction)
        with original_guard(current_operation_id) as receipt:
            facts_guard_active = True
            try:
                yield receipt
            finally:
                facts_guard_active = False

    def guarded_save(scope: MemoryScope, **kwargs) -> None:
        observed_commit_guard.append(facts_guard_active)
        original_save(scope, **kwargs)

    monkeypatch.setattr(
        fresh._project_facts,
        "operation_receipt_guard",
        guarded_receipt,
    )
    monkeypatch.setattr(fresh._memory, "_save_scope_locked", guarded_save)

    replay = fresh.write_result(
        "persist the confirmed dependency",
        [],
        operation_id=operation_id,
        provenance=provenance,
    )

    assert replay.project_facts_effect.status == "replayed"
    assert observed_lock_order == [True]
    assert observed_commit_guard == [True]
