from __future__ import annotations

import json
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
    MemoryWriteResult,
    ProjectFactsEffect,
)
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
        self.shadow_calls = 0

    def reflect(self, *_args, **_kwargs) -> ReflectionResult:
        return self.result

    def complete_shadow(self, *_args, **_kwargs) -> None:
        self.shadow_calls += 1
        if self.shadow_error is not None:
            raise self.shadow_error


class _FactsFailure:
    def __init__(self) -> None:
        self.calls = 0

    def observe_dependencies_once(self, *_args, **_kwargs) -> ProjectFactsEffect:
        self.calls += 1
        raise OSError("controlled Project Facts failure")


class _FactsSpy:
    def __init__(self) -> None:
        self.calls = 0

    def observe_dependencies_once(self, *_args, **_kwargs) -> ProjectFactsEffect:
        self.calls += 1
        return ProjectFactsEffect(status="committed", added_count=1)


def _operation(index: int) -> str:
    return f"memoryop_{index:032x}"


def _downgrade_receipt_to_legacy_exact(
    authority_path: Path,
    *,
    exact_digest: str,
) -> None:
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    reference = MemoryAuthorityReceiptReference.from_dict(
        authority["write_receipts"][0]
    )
    raw = MemoryBlobStore(authority_path.parent).read_json(reference.blob)
    commit = MemoryAuthorityCommit.from_dict(raw)
    legacy_receipt = commit.to_dict()
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


def _claim(
    index: int,
    *,
    claim_type: str = "approach",
    semantic_key: str | None = None,
    statement: str | None = None,
) -> ReflectionClaim:
    return ReflectionClaim(
        claim_id=f"claim-{index:06d}",
        claim_type=claim_type,  # type: ignore[arg-type]
        semantic_key=semantic_key or f"atomic_lesson_{index}",
        statement=statement or f"Apply verified atomic lesson {index}.",
        evidence_ids=[f"event-{index}"],
        epistemic_status="confirmed",
        verification_ids=[f"verification-{index}"],
    )


def _reflection_result(
    claims: list[ReflectionClaim],
    *,
    libraries: tuple[str, ...] = (),
) -> ReflectionResult:
    candidate = ReflectionCandidate(
        task_summary="atomic reflection write",
        outcome="success",
        claims=claims,
    )
    evidence = TaskEvidence(
        libraries=[
            LibraryEvidence(
                name=name,
                status="confirmed",
                event_ids=(f"event-dependency-{index}",),
            )
            for index, name in enumerate(libraries, start=1)
        ],
        outcome="success",
    )
    return ReflectionResult(
        task_summary="atomic reflection write",
        success=True,
        key_decisions=[],
        errors_encountered=[],
        lessons_learned=[],
        suggested_improvements=[],
        confidence=0.95,
        task_evidence=evidence,
        reflection_candidate=candidate,
        claim_validation=ClaimValidationResult(valid_claims=claims),
        value_decision=ReflectionValueDecision(
            accepted=True,
            reason_codes=["accepted_durable_reflection"],
            durable_signals=["verified_approach"],
            accepted_claim_ids=[claim.claim_id for claim in claims],
        ),
        structured_claims=claims,
    )


def _pipeline(
    tmp_path: Path,
    result: ReflectionResult,
    *,
    workspace_name: str = "workspace",
    shadow_error: BaseException | None = None,
) -> tuple[MemoryPipeline, MemoryManager, _ReflectionStub, Path, Path]:
    workspace = tmp_path / workspace_name
    workspace.mkdir(exist_ok=True)
    data_root = tmp_path / f"{workspace_name}-data" / ".mini-code"
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    reflection = _ReflectionStub(result, shadow_error=shadow_error)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        reflection_engine=reflection,
    )
    authority = workspace / ".mini-code-memory" / "memory.json"
    return pipeline, manager, reflection, workspace, authority


def _fresh_pipeline(
    workspace: Path,
    data_root: Path,
    result: ReflectionResult,
) -> tuple[MemoryPipeline, MemoryManager]:
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    pipeline = MemoryPipeline(manager)
    pipeline.initialize(
        workspace_path=str(workspace),
        reflection_engine=_ReflectionStub(result),
    )
    return pipeline, manager


def _project_entries(manager: MemoryManager):
    return manager.memories[MemoryScope.PROJECT].entries


def _terminal_no_effect_result(kind: str) -> ReflectionResult:
    if kind == "reflection_not_persistable":
        result = _reflection_result([])
        return replace(
            result,
            value_decision=ReflectionValueDecision(
                accepted=False,
                reason_codes=["no_durable_claims"],
                durable_signals=[],
                accepted_claim_ids=[],
            ),
        )
    if kind == "dependency_without_fact_plan":
        dependency = _claim(
            70,
            claim_type="dependency",
            semantic_key="dependency_without_confirmed_evidence",
            statement="A dependency claim lacks matching confirmed evidence.",
        )
        return _reflection_result([dependency])
    raise AssertionError(f"unsupported terminal no-effect kind: {kind}")


def _retry_payload(kind: str) -> tuple[str, ReflectionResult]:
    if kind == "lesson":
        return (
            "persist a verified lesson",
            _reflection_result([_claim(71)]),
        )
    if kind == "conversation":
        return (
            "I prefer concise answers.",
            _terminal_no_effect_result("reflection_not_persistable"),
        )
    if kind == "facts":
        dependency = _claim(
            72,
            claim_type="dependency",
            semantic_key="dependency_httpx",
            statement="The accepted evidence confirms httpx.",
        )
        dependency.evidence_ids = ["event-dependency-1"]
        return (
            "persist a confirmed dependency",
            _reflection_result([dependency], libraries=("httpx",)),
        )
    raise AssertionError(f"unsupported retry payload kind: {kind}")


def _optional_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def test_three_claim_legacy_write_replaces_memory_authority_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = [_claim(1), _claim(2), _claim(3)]
    pipeline, manager, _reflection, _workspace, authority = _pipeline(
        tmp_path,
        _reflection_result(claims),
    )
    original_atomic_write = manager._atomic_write
    authority_replaces: list[Path] = []

    def tracking_atomic_write(target: Path, content: str) -> None:
        if target == authority:
            authority_replaces.append(target)
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", tracking_atomic_write)

    representative = pipeline.write("persist three claims", [])

    assert representative is not None
    assert len(_project_entries(manager)) == 3
    assert authority_replaces == [authority]


def test_second_staged_mutation_failure_leaves_no_authority_or_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = [_claim(1), _claim(2), _claim(3)]
    pipeline, manager, _reflection, workspace, authority = _pipeline(
        tmp_path,
        _reflection_result(claims),
    )
    original_add_entry = manager.add_entry
    add_calls = 0

    def fail_second_add(*args, **kwargs):
        nonlocal add_calls
        add_calls += 1
        if add_calls == 2:
            raise RuntimeError("controlled second staged mutation failure")
        return original_add_entry(*args, **kwargs)

    monkeypatch.setattr(manager, "add_entry", fail_second_add)

    representative = pipeline.write("stage three claims", [])

    assert representative is None
    assert authority.exists() is False
    assert (workspace / ".mini-code-memory" / "approval_audit.json").exists() is False
    assert (workspace / ".mini-code-memory" / "MEMORY.md").exists() is False
    assert _project_entries(manager) == []
    assert manager.approval_audit[MemoryScope.PROJECT] == []


def test_write_result_exposes_all_versions_and_write_returns_representative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = [_claim(1), _claim(2), _claim(3)]
    pipeline, manager, _reflection, _workspace, _authority = _pipeline(
        tmp_path,
        _reflection_result(claims),
    )

    result = pipeline.write_result(
        "persist three claims",
        [],
        operation_id=_operation(1),
    )

    assert isinstance(result, MemoryWriteResult)
    assert result.authority_commit is not None
    assert result.authority_kind == "lesson"
    assert len(result.authority_versions) == 3
    entries = {entry.id: entry for entry in _project_entries(manager)}
    assert result.authority_versions == tuple(
        (item.entry_id, item.authority_digest)
        for item in result.authority_commit.materializations
    )
    assert {
        (entry_id, entries[entry_id].approval_content_hash)
        for entry_id, _digest in result.authority_versions
    } == set(result.authority_versions)
    assert pipeline.last_written_versions == result.authority_versions
    assert pipeline.last_written_ids == tuple(
        entry_id for entry_id, _digest in result.authority_versions
    )

    captured: list[MemoryWriteResult] = []
    original_write_result = pipeline.write_result

    def capture_result(*args, **kwargs) -> MemoryWriteResult:
        observed = original_write_result(*args, **kwargs)
        captured.append(observed)
        return observed

    monkeypatch.setattr(pipeline, "write_result", capture_result)
    representative = pipeline.write("persist three claims through compatibility", [])

    assert len(captured) == 1
    assert representative == captured[0].representative_id
    assert representative == pipeline.last_written_ids[0]


def test_same_operation_and_payload_replays_across_fresh_pipeline_without_recurrence(
    tmp_path: Path,
) -> None:
    claims = [_claim(1), _claim(2)]
    reflection_result = _reflection_result(claims)
    pipeline, manager, _reflection, workspace, authority = _pipeline(
        tmp_path,
        reflection_result,
    )
    first = pipeline.write_result(
        "persist replayable claims",
        [],
        operation_id=_operation(1),
    )
    before = authority.read_bytes()
    recurrence_before = {
        entry.id: entry.metadata.get("recurrence", 1)
        for entry in _project_entries(manager)
    }

    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        reflection_result,
    )
    replay = fresh.write_result(
        "persist replayable claims",
        [],
        operation_id=_operation(1),
    )

    assert first.authority_commit is not None
    assert replay.authority_commit == first.authority_commit.as_replay()
    assert replay.authority_versions == first.authority_versions
    assert authority.read_bytes() == before
    assert {
        entry.id: entry.metadata.get("recurrence", 1)
        for entry in _project_entries(fresh_manager)
    } == recurrence_before


def test_pipeline_upgrades_legacy_exact_lesson_receipt_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = [_claim(1)]
    reflection_result = _reflection_result(claims)
    pipeline, manager, _reflection, workspace, authority = _pipeline(
        tmp_path,
        reflection_result,
    )
    captured: dict[str, object] = {}
    original_commit = manager.commit_reflection_batch

    def capture_commit(**kwargs):
        captured.update(kwargs)
        return original_commit(**kwargs)

    monkeypatch.setattr(manager, "commit_reflection_batch", capture_commit)
    first = pipeline.write_result(
        "upgrade a legacy lesson receipt",
        [],
        operation_id=_operation(41),
    )
    assert first.authority_commit is not None
    exact_digest = captured["authority_payload_digest"]
    assert isinstance(exact_digest, str)

    _downgrade_receipt_to_legacy_exact(
        authority,
        exact_digest=exact_digest,
    )

    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        reflection_result,
    )
    recurrence_before = (
        fresh_manager.memories[MemoryScope.PROJECT]
        .entries[0]
        .metadata.get("recurrence", 1)
    )
    replay = fresh.write_result(
        "upgrade a legacy lesson receipt",
        [],
        operation_id=_operation(41),
    )

    assert replay.authority_commit is not None
    assert replay.authority_commit.replayed is True
    assert replay.authority_commit.payload_digest == first.payload_digest
    assert replay.authority_versions == first.authority_versions
    assert (
        fresh_manager.memories[MemoryScope.PROJECT]
        .entries[0]
        .metadata.get(
            "recurrence",
            1,
        )
        == recurrence_before
    )
    upgraded = json.loads(authority.read_text(encoding="utf-8"))
    assert upgraded["write_receipts"][0]["payloadDigest"] == first.payload_digest
    assert upgraded["pipeline_operation_receipts"] == [
        {
            "operationId": _operation(41),
            "aggregateDigest": first.payload_digest,
        }
    ]


def test_pipeline_upgrades_legacy_exact_conversation_receipt_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reflection_result = _reflection_result([])
    pipeline, manager, _reflection, workspace, _authority = _pipeline(
        tmp_path,
        reflection_result,
    )
    captured: dict[str, object] = {}
    original_commit = manager.commit_conversation_fact

    def capture_commit(**kwargs):
        captured.update(kwargs)
        return original_commit(**kwargs)

    monkeypatch.setattr(manager, "commit_conversation_fact", capture_commit)
    task = "我偏好简洁的回答。"
    first = pipeline.write_result(task, [], operation_id=_operation(42))
    assert first.authority_commit is not None
    assert first.authority_kind == "conversation_fact"
    exact_digest = captured["authority_payload_digest"]
    assert isinstance(exact_digest, str)
    user_authority = manager.paths.user_memory / "memory.json"

    _downgrade_receipt_to_legacy_exact(
        user_authority,
        exact_digest=exact_digest,
    )

    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        reflection_result,
    )
    updated_at_before = fresh_manager.memories[MemoryScope.USER].entries[0].updated_at
    replay = fresh.write_result(task, [], operation_id=_operation(42))

    assert replay.authority_commit is not None
    assert replay.authority_commit.replayed is True
    assert replay.authority_commit.payload_digest == first.payload_digest
    assert replay.authority_versions == first.authority_versions
    assert fresh_manager.memories[MemoryScope.USER].entries[0].updated_at == (
        updated_at_before
    )
    upgraded = json.loads(user_authority.read_text(encoding="utf-8"))
    assert upgraded["write_receipts"][0]["payloadDigest"] == first.payload_digest
    assert upgraded["pipeline_operation_receipts"] == [
        {
            "operationId": _operation(42),
            "aggregateDigest": first.payload_digest,
        }
    ]


def test_same_operation_with_changed_payload_reports_conflict_without_mutation(
    tmp_path: Path,
) -> None:
    first_result = _reflection_result([_claim(1)])
    pipeline, manager, _reflection, workspace, authority = _pipeline(
        tmp_path,
        first_result,
    )
    committed = pipeline.write_result(
        "persist operation payload",
        [],
        operation_id=_operation(1),
    )
    assert committed.authority_commit is not None
    before = authority.read_bytes()
    before_versions = committed.authority_versions

    changed = _reflection_result(
        [
            _claim(
                1,
                statement="Apply a materially changed atomic lesson.",
            )
        ]
    )
    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        changed,
    )
    conflict = fresh.write_result(
        "persist operation payload",
        [],
        operation_id=_operation(1),
    )

    assert conflict.authority_commit is None
    assert "memory_write_conflict" in conflict.reason_codes
    assert conflict.authority_versions == ()
    assert authority.read_bytes() == before
    assert (
        tuple(
            (entry.id, entry.approval_content_hash)
            for entry in _project_entries(fresh_manager)
            if entry.is_active
        )
        == before_versions
    )


def test_pipeline_persists_only_exact_validator_and_value_gate_intersection(
    tmp_path: Path,
) -> None:
    validated = _claim(
        1,
        statement="Persist the exact validator-authorized lesson.",
    )
    same_id_unvalidated = _claim(
        1,
        statement="Do not persist this same-ID unvalidated proposal.",
    )
    rejected_dependency = _claim(
        2,
        claim_type="dependency",
        semantic_key="dependency_unvalidated_pkg",
        statement="Do not project this rejected dependency.",
    )
    reflection_result = _reflection_result(
        [same_id_unvalidated, rejected_dependency],
        libraries=("unvalidated_pkg",),
    )
    reflection_result = replace(
        reflection_result,
        claim_validation=ClaimValidationResult(
            valid_claims=[validated],
            rejected_claims=[rejected_dependency],
        ),
        value_decision=replace(
            reflection_result.value_decision,
            accepted_claim_ids=[
                validated.claim_id,
                rejected_dependency.claim_id,
            ],
        ),
    )
    pipeline, manager, _reflection, _workspace, _authority = _pipeline(
        tmp_path,
        reflection_result,
    )
    facts = _FactsSpy()
    pipeline._project_facts = facts

    result = pipeline.write_result(
        "persist only authorized claims",
        [],
        operation_id=_operation(1),
    )

    assert result.authority_commit is not None
    entries = _project_entries(manager)
    assert len(entries) == 1
    assert "validator-authorized" in entries[0].content
    assert "same-ID unvalidated" not in entries[0].content
    assert facts.calls == 0
    assert result.project_facts_effect.status == "not_applicable"


def test_project_facts_project_only_library_evidence_bound_to_accepted_claim(
    tmp_path: Path,
) -> None:
    accepted_dependency = _claim(
        1,
        claim_type="dependency",
        semantic_key="dependency_httpx",
        statement="The accepted evidence confirms httpx.",
    )
    accepted_dependency.evidence_ids = ["event-dependency-1"]
    rejected_dependency = _claim(
        2,
        claim_type="dependency",
        semantic_key="dependency_evilpkg",
        statement="This dependency claim was rejected.",
    )
    rejected_dependency.evidence_ids = ["event-dependency-2"]
    reflection_result = _reflection_result(
        [accepted_dependency, rejected_dependency],
        libraries=("httpx", "evilpkg"),
    )
    reflection_result = replace(
        reflection_result,
        claim_validation=ClaimValidationResult(
            valid_claims=[accepted_dependency],
            rejected_claims=[rejected_dependency],
        ),
        value_decision=replace(
            reflection_result.value_decision,
            accepted_claim_ids=[
                accepted_dependency.claim_id,
                rejected_dependency.claim_id,
            ],
        ),
    )
    pipeline, manager, _reflection, _workspace, _authority = _pipeline(
        tmp_path,
        reflection_result,
    )

    result = pipeline.write_result(
        "project only accepted dependency evidence",
        [],
        operation_id=_operation(1),
    )

    assert result.authority_commit is None
    assert result.project_facts_effect.added_count == 1
    assert set(pipeline._project_facts.snapshot()) == {"dependency:httpx"}
    assert _project_entries(manager) == []


def test_project_facts_failure_cannot_erase_committed_lesson(
    tmp_path: Path,
) -> None:
    dependency = _claim(
        1,
        claim_type="dependency",
        semantic_key="dependency_httpx",
        statement="The manifest confirms dependency httpx.",
    )
    dependency.evidence_ids = ["event-dependency-1"]
    lesson = _claim(2)
    pipeline, manager, _reflection, _workspace, authority = _pipeline(
        tmp_path,
        _reflection_result([dependency, lesson], libraries=("httpx",)),
    )
    facts = _FactsFailure()
    pipeline._project_facts = facts

    result = pipeline.write_result(
        "persist lesson and Project Facts",
        [],
        operation_id=_operation(1),
    )

    assert result.authority_commit is not None
    assert result.authority_kind == "lesson"
    assert len(result.authority_versions) == 1
    assert result.project_facts_effect.status == "failed"
    assert result.project_facts_effect.failure_code is not None
    assert facts.calls == 1
    assert authority.exists()
    assert len(_project_entries(manager)) == 1


def test_lesson_authority_failure_prevents_project_facts_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency = _claim(
        1,
        claim_type="dependency",
        semantic_key="dependency_httpx",
        statement="The manifest confirms dependency httpx.",
    )
    dependency.evidence_ids = ["event-dependency-1"]
    lesson = _claim(2)
    pipeline, manager, _reflection, _workspace, authority = _pipeline(
        tmp_path,
        _reflection_result([dependency, lesson], libraries=("httpx",)),
    )
    facts = _FactsSpy()
    pipeline._project_facts = facts

    def fail_authority(**_kwargs):
        raise MemoryStoreConflict("controlled authority failure")

    monkeypatch.setattr(manager, "commit_reflection_batch", fail_authority)

    result = pipeline.write_result(
        "fail lesson before Project Facts",
        [],
        operation_id=_operation(1),
    )

    assert result.authority_commit is None
    assert "memory_write_conflict" in result.reason_codes
    assert result.project_facts_effect.status not in {"committed", "replayed"}
    assert facts.calls == 0
    assert authority.exists() is False
    assert _project_entries(manager) == []


def test_shadow_cancellation_reports_committed_result_instead_of_masking_it(
    tmp_path: Path,
) -> None:
    claims = [_claim(1), _claim(2)]
    pipeline, manager, reflection, _workspace, authority = _pipeline(
        tmp_path,
        _reflection_result(claims),
        shadow_error=TurnCancellationRequested("cancel after commit"),
    )

    result = pipeline.write_result(
        "persist before shadow cancellation",
        [],
        operation_id=_operation(1),
    )

    assert result.authority_commit is not None
    assert len(result.authority_versions) == 2
    assert result.post_commit_interrupted is True
    assert result.representative_id is not None
    assert reflection.shadow_calls == 1
    assert authority.exists()
    assert len(_project_entries(manager)) == 2


@pytest.mark.parametrize(
    ("no_effect_kind", "expected_reason", "expected_facts_status"),
    [
        (
            "reflection_not_persistable",
            "reflection_not_persistable",
            "not_applicable",
        ),
        (
            "dependency_without_fact_plan",
            "project_facts_only",
            "skipped",
        ),
    ],
)
def test_terminal_no_effect_exact_replay_is_byte_stable(
    tmp_path: Path,
    no_effect_kind: str,
    expected_reason: str,
    expected_facts_status: str,
) -> None:
    reflection_result = _terminal_no_effect_result(no_effect_kind)
    pipeline, manager, _reflection, workspace, authority = _pipeline(
        tmp_path,
        reflection_result,
    )
    operation_id = _operation(50)
    task = "review a completed reflection with no durable output"

    first = pipeline.write_result(task, [], operation_id=operation_id)

    assert first.authority_commit is None
    assert first.reason_codes == (expected_reason,)
    assert first.project_facts_effect.status == expected_facts_status
    assert authority.exists()
    before = authority.read_bytes()
    persisted = json.loads(before)
    assert persisted["write_receipts"] == []
    assert persisted["pipeline_operation_receipts"] == [
        {
            "operationId": operation_id,
            "aggregateDigest": first.payload_digest,
        }
    ]

    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        reflection_result,
    )
    replay = fresh.write_result(task, [], operation_id=operation_id)

    assert replay.authority_commit is None
    assert replay.payload_digest == first.payload_digest
    assert replay.reason_codes == first.reason_codes
    assert replay.project_facts_effect == first.project_facts_effect
    assert authority.read_bytes() == before
    assert _project_entries(fresh_manager) == []
    assert fresh_manager.memories[MemoryScope.USER].entries == []


@pytest.mark.parametrize(
    "no_effect_kind",
    ["reflection_not_persistable", "dependency_without_fact_plan"],
)
@pytest.mark.parametrize("retry_kind", ["lesson", "conversation", "facts"])
def test_terminal_no_effect_operation_rejects_later_durable_payload(
    tmp_path: Path,
    no_effect_kind: str,
    retry_kind: str,
) -> None:
    operation_id = _operation(51)
    initial_result = _terminal_no_effect_result(no_effect_kind)
    pipeline, manager, _reflection, workspace, authority = _pipeline(
        tmp_path,
        initial_result,
    )
    first = pipeline.write_result(
        "review a completed reflection with no durable output",
        [],
        operation_id=operation_id,
    )
    user_authority = manager.paths.user_memory / "memory.json"
    facts_authority = workspace / ".mini-code-memory" / "project_facts.json"
    project_before = _optional_bytes(authority)
    user_before = _optional_bytes(user_authority)
    facts_before = _optional_bytes(facts_authority)
    retry_task, retry_result = _retry_payload(retry_kind)

    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        retry_result,
    )
    conflict = fresh.write_result(
        retry_task,
        [],
        operation_id=operation_id,
    )

    assert first.authority_commit is None
    assert conflict.authority_commit is None
    assert conflict.reason_codes == ("memory_write_conflict",)
    assert conflict.project_facts_effect.status == "not_applicable"
    assert _optional_bytes(authority) == project_before
    assert _optional_bytes(user_authority) == user_before
    assert _optional_bytes(facts_authority) == facts_before
    assert _project_entries(fresh_manager) == []
    assert fresh_manager.memories[MemoryScope.USER].entries == []


def test_transient_reflection_failure_leaves_operation_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson_result = _reflection_result([_claim(73)])
    pipeline, manager, reflection, workspace, authority = _pipeline(
        tmp_path,
        lesson_result,
    )
    operation_id = _operation(52)

    def fail_transiently(*_args, **_kwargs):
        raise OSError("controlled transient reflection failure")

    monkeypatch.setattr(reflection, "reflect", fail_transiently)
    failed = pipeline.write_result(
        "persist after a transient reflection failure",
        [],
        operation_id=operation_id,
    )

    assert failed.authority_commit is None
    assert failed.reason_codes == ("reflection_write_failed",)
    assert authority.exists() is False

    fresh, fresh_manager = _fresh_pipeline(
        workspace,
        manager._data_root,
        lesson_result,
    )
    recovered = fresh.write_result(
        "persist after a transient reflection failure",
        [],
        operation_id=operation_id,
    )

    assert recovered.authority_commit is not None
    assert recovered.reason_codes == ("lesson_authority_committed",)
    assert authority.exists()
    assert len(_project_entries(fresh_manager)) == 1
