from __future__ import annotations

import json
from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.conversation import (
    _apply_run_outcome_memory_feedback,
    _apply_written_run_outcome_feedback,
)
from minicode.memory import (
    MemoryApprovalPolicy,
    MemoryManager,
    MemoryScope,
    MemoryTier,
)
from minicode.memory_pipeline import MemoryPipeline, MemoryPromptReceipt
from minicode.run_journal import (
    RunJournal,
    RunJournalStorageError,
    RunJournalValidationError,
)


def _manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MemoryManager:
    monkeypatch.setattr(
        memory_module,
        "MINI_CODE_DIR",
        tmp_path / "home" / ".mini-code",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return MemoryManager(project_root=workspace)


def _entry(
    manager: MemoryManager,
    content: str,
) -> tuple[str, str]:
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        content,
        tags=["pytest", "checkout"],
        source="test",
    )
    assert entry is not None
    assert len(entry.approval_content_hash) == 64
    return entry.id, entry.approval_content_hash


class _RunMemoryJournal:
    def __init__(
        self,
        versions: tuple[tuple[str, str], ...],
        *,
        written_versions: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.versions = versions
        self.written_versions = written_versions

    def get_rendered_memory_versions(
        self,
        _run_id: str,
    ) -> tuple[tuple[str, str], ...]:
        return self.versions

    def get_rendered_memory_ids(self, _run_id: str) -> tuple[str, ...]:
        return tuple(entry_id for entry_id, _digest in self.versions)

    def get_written_memory_versions(
        self,
        _run_id: str,
    ) -> tuple[tuple[str, str], ...]:
        return self.written_versions


class _LegacyRunMemoryJournal:
    def __init__(self, entry_ids: tuple[str, ...]) -> None:
        self.entry_ids = entry_ids

    def get_rendered_memory_ids(self, _run_id: str) -> tuple[str, ...]:
        return self.entry_ids


def test_run_journal_materializations_are_versioned_and_appendable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    run = journal.create_run(title="versioned Memory", source="gateway")
    journal.transition(run.id, "running")
    first = (
        "project-1785082406796413000-b6ecf281",
        "a" * 64,
    )
    second = (
        "user-1785082406796413001-a1b2c3d4",
        "b" * 64,
    )

    journal.record_rendered_memory_versions(run.id, [first])
    journal.record_rendered_memory_versions(run.id, [second])
    journal.transition(run.id, "completed")

    reopened = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    assert reopened.get_rendered_memory_versions(run.id) == (first, second)


def test_run_journal_written_versions_append_without_crossing_sidecars(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = RunJournal(
        workspace,
        data_dir=tmp_path / "home" / ".mini-code",
    )
    run = journal.create_run(title="versioned writes", source="gateway")
    journal.transition(run.id, "running")
    first = ("project-1785082406796413000-b6ecf281", "a" * 64)
    replacement = (first[0], "b" * 64)
    written = ("user-1785082406796413001-a1b2c3d4", "c" * 64)

    journal.record_rendered_memory_versions(run.id, [first])
    journal.record_rendered_memory_versions(run.id, [first, replacement])
    journal.record_written_memory_versions(run.id, [written])
    journal.record_written_memory_versions(run.id, [written])
    journal.transition(run.id, "completed")

    reopened = RunJournal(workspace, data_dir=tmp_path / "home" / ".mini-code")
    assert reopened.get_rendered_memory_versions(run.id) == (
        first,
        replacement,
    )
    assert reopened.get_written_memory_versions(run.id) == (written,)
    assert reopened.get_rendered_memory_ids(run.id) == (first[0],)
    assert reopened.get_written_memory_ids(run.id) == (written[0],)


def test_run_journal_version_sidecar_rejects_invalid_digest_and_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="unsafe versions", source="gateway")
    journal.transition(run.id, "running")

    with pytest.raises(RunJournalValidationError):
        journal.record_rendered_memory_versions(
            run.id,
            [("project-1785082406796413000-b6ecf281", "A" * 64)],
        )

    run_dir = next(data_dir.rglob(run.id))
    outside = tmp_path / "outside.json"
    outside.write_text('{"preserve":true}\n', encoding="utf-8")
    sidecar = run_dir / "memory_rendered_versions.json"
    sidecar.symlink_to(outside)

    with pytest.raises(RunJournalStorageError):
        journal.record_rendered_memory_versions(
            run.id,
            [("project-1785082406796413000-b6ecf281", "a" * 64)],
        )
    assert sidecar.is_symlink()
    assert outside.read_text(encoding="utf-8") == '{"preserve":true}\n'


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("kind", "written"),
        ("digestSchema", "different-digest-schema-v1"),
    ],
)
def test_run_journal_version_sidecar_rejects_schema_confusion(
    tmp_path: Path,
    field: str,
    invalid_value: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="schema binding", source="gateway")
    journal.transition(run.id, "running")
    version = ("project-1785082406796413000-b6ecf281", "a" * 64)
    journal.record_rendered_memory_versions(run.id, [version])
    sidecar = next(data_dir.rglob(run.id)) / "memory_rendered_versions.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload[field] = invalid_value
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RunJournalStorageError):
        journal.get_rendered_memory_versions(run.id)


def test_delayed_feedback_cannot_mutate_replacement_content_under_the_same_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, old_digest = _entry(
        manager,
        "OLD VERSION: use isolated checkout fixtures.",
    )
    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        "NEW VERSION: use transactional checkout fixtures.",
    )
    manager.approve_entry(
        entry_id,
        actor="test",
        reason="approve replacement",
    )

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        _RunMemoryJournal(((entry_id, old_digest),)),
        "run-old-version",
        "reject",
    )

    current = MemoryManager(
        project_root=tmp_path / "workspace"
    ).memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert current.content.startswith("NEW VERSION")
    assert current.is_active is True
    assert current.failure_count == 0
    assert current.corroborated_failure_count == 0


@pytest.mark.parametrize("signal", ["correct", "reject"])
def test_whole_response_negative_feedback_is_weak_and_never_quarantines_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal: str,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    first = _entry(manager, "Use isolated checkout fixtures.")
    second = _entry(manager, "Keep checkout currency arithmetic in Decimal.")

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        _RunMemoryJournal((first, second)),
        f"run-whole-response-{signal}",
        signal,
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    for entry_id, _digest in (first, second):
        entry = current.memories[MemoryScope.PROJECT]._id_index[entry_id]
        assert entry.is_active is True
        assert entry.failure_count == 1
        assert entry.corroborated_failure_count == 0


def test_whole_response_acceptance_is_version_bound_ordinary_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    materialization = _entry(manager, "Use isolated checkout fixtures.")

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        _RunMemoryJournal((materialization,)),
        "run-whole-response-accept",
        "accept",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[
        materialization[0]
    ]
    assert entry.success_count == 1
    assert entry.corroborated_success_count == 0


def test_legacy_id_only_run_feedback_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, _digest = _entry(manager, "Use isolated checkout fixtures.")

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        _LegacyRunMemoryJournal((entry_id,)),
        "run-legacy-id-only",
        "reject",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.failure_count == 0
    assert entry.corroborated_failure_count == 0
    assert entry.is_active is True


def test_unversioned_draft_attribution_cannot_widen_delayed_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    canonical = _entry(manager, "Use isolated checkout fixtures.")
    unversioned = _entry(manager, "Keep transaction updates atomic.")
    workspace = tmp_path / "workspace"
    journal = RunJournal(workspace, data_dir=tmp_path / "journal")
    run = journal.create_run(title="Draft feedback isolation", source="gateway")
    journal.transition(run.id, "running")
    journal.record_rendered_memory_versions(run.id, [canonical])
    # Even if a draft ID happens to match a durable entry, its bare rendering
    # attribution must never grant a feedback version for that entry.
    journal.record_rendered_memory_ids(run.id, [canonical[0], unversioned[0]])
    journal.transition(run.id, "completed")

    assert journal.get_rendered_memory_ids(run.id) == (
        canonical[0], unversioned[0],
    )
    assert journal.get_rendered_memory_versions(run.id) == (canonical,)
    _apply_run_outcome_memory_feedback(workspace, journal, run.id, "reject")

    current = MemoryManager(project_root=workspace)
    entries = current.memories[MemoryScope.PROJECT]._id_index
    assert entries[canonical[0]].failure_count == 1
    assert entries[unversioned[0]].failure_count == 0


def test_matching_version_feedback_is_idempotent_per_delivery_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    materialization = _entry(manager, "Use isolated checkout fixtures.")
    journal = _RunMemoryJournal((materialization,))

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        journal,
        "run-idempotent",
        "accept",
    )
    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        journal,
        "run-idempotent",
        "accept",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[
        materialization[0]
    ]
    assert entry.success_count == 1


def test_pipeline_feedback_is_bound_to_the_materialized_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, old_digest = _entry(
        manager,
        "OLD VERSION: use isolated checkout fixtures.",
    )
    pipeline = MemoryPipeline(manager)
    receipt = MemoryPromptReceipt(
        prompt_text="## Relevant Context from Memory\n\n1. old",
        selected_ids=(entry_id,),
        rendered_ids=(entry_id,),
        authority_revision="revision-old",
        receipt_id="receipt-old",
        rendered_versions=((entry_id, old_digest),),
    )
    pipeline.commit_prompt_receipt(receipt)

    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        "NEW VERSION: use transactional checkout fixtures.",
    )
    manager.approve_entry(
        entry_id,
        actor="test",
        reason="approve replacement",
    )
    pipeline.feedback(False, observation_id="run-pipeline-old-version")

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.content.startswith("NEW VERSION")
    assert entry.failure_count == 0
    assert entry.is_active is True


def test_content_update_resets_version_scoped_feedback_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _entry(manager, "Use isolated checkout fixtures.")
    manager.record_retrievals_and_injections([entry_id], [entry_id])
    manager.record_feedback(
        [entry_id],
        True,
        observation_id="run-old-outcome",
        expected_versions=[(entry_id, digest)],
    )
    manager.record_corroborated_feedback(
        [entry_id],
        True,
        source="independent_verification",
        observation_id="verify-old-version",
        expected_versions=[(entry_id, digest)],
    )

    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        "Use transactional checkout fixtures.",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.retrieval_count == 0
    assert entry.injection_count == 0
    assert entry.usage_count == 0
    assert entry.success_count == 0
    assert entry.failure_count == 0
    assert entry.usefulness_score == 0.0
    assert entry.corroborated_success_count == 0
    assert entry.corroborated_failure_count == 0
    assert entry.corroborated_usefulness_score == 0.0
    assert entry.feedback_observations == {}
    assert entry.last_accessed == 0.0


@pytest.mark.parametrize(
    ("operation", "tag"),
    [("add", "transactional"), ("remove", "checkout")],
)
def test_tag_version_changes_reset_prior_feedback_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    tag: str,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _entry(manager, "Use isolated checkout fixtures.")
    manager.record_retrievals_and_injections([entry_id], [entry_id])
    manager.record_feedback(
        [entry_id],
        True,
        observation_id="run-before-tag-change",
        expected_versions=[(entry_id, digest)],
    )

    changed = (
        manager.add_tag(MemoryScope.PROJECT, entry_id, tag)
        if operation == "add"
        else manager.remove_tag(MemoryScope.PROJECT, entry_id, tag)
    )

    assert changed is True
    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.approval_content_hash != digest
    assert entry.retrieval_count == 0
    assert entry.injection_count == 0
    assert entry.usage_count == 0
    assert entry.success_count == 0
    assert entry.failure_count == 0
    assert entry.feedback_observations == {}


def test_stale_persisted_hash_cannot_carry_feedback_into_loaded_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _entry(manager, "Use isolated checkout fixtures.")
    manager.record_retrievals_and_injections([entry_id], [entry_id])
    manager.record_feedback(
        [entry_id],
        True,
        observation_id="run-before-disk-tamper",
        expected_versions=[(entry_id, digest)],
    )
    authority = tmp_path / "workspace" / ".mini-code-memory" / "memory.json"
    payload = json.loads(authority.read_text(encoding="utf-8"))
    stored = next(item for item in payload["entries"] if item["id"] == entry_id)
    stored["content"] = "Use transactional checkout fixtures."
    authority.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = MemoryManager(project_root=tmp_path / "workspace")
    entry = reloaded.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.approval_status == "pending"
    assert entry.approval_content_hash != digest
    assert entry.retrieval_count == 0
    assert entry.injection_count == 0
    assert entry.success_count == 0
    assert entry.feedback_observations == {}


def test_approval_hash_rebind_resets_unbound_live_object_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use isolated checkout fixtures.",
        tags=["pytest", "checkout"],
        source="test",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    digest = entry.approval_content_hash
    manager.record_retrievals_and_injections([entry.id], [entry.id])
    manager.record_feedback(
        [entry.id],
        True,
        observation_id="run-before-live-mutation",
        expected_versions=[(entry.id, digest)],
    )
    entry.content = "Use transactional checkout fixtures."
    entry.invalidate_tokens()

    message = manager.approve_entry(entry.id, actor="test")

    assert "content hash changed" in message
    current = MemoryManager(project_root=tmp_path / "workspace")
    rebound = current.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert rebound.approval_status == "pending"
    assert rebound.approval_content_hash != digest
    assert rebound.retrieval_count == 0
    assert rebound.injection_count == 0
    assert rebound.success_count == 0
    assert rebound.feedback_observations == {}


def test_fresh_long_term_version_is_not_archived_as_epoch_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use isolated checkout fixtures.",
        tags=["pytest", "checkout"],
        source="test",
        tier=MemoryTier.LONG_TERM,
    )
    assert entry is not None
    manager.record_retrievals_and_injections([entry.id], [entry.id])
    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry.id,
        "Use transactional checkout fixtures.",
    )
    updated = manager.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert updated.last_accessed == 0.0

    manager.promote_memories()

    current = manager.memories[MemoryScope.PROJECT]._id_index[entry.id]
    assert current.tier is MemoryTier.LONG_TERM
    assert current.lifecycle_status == "active"


def test_integrity_category_repair_resets_the_prior_authority_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, digest = _entry(manager, "Use isolated checkout fixtures.")
    manager.record_retrievals_and_injections([entry_id], [entry_id])
    manager.record_feedback(
        [entry_id],
        True,
        observation_id="run-before-category-repair",
        expected_versions=[(entry_id, digest)],
    )
    live = manager.memories[MemoryScope.PROJECT]._id_index[entry_id]
    live.category = ""

    manager._recover_scope(MemoryScope.PROJECT)

    repaired = MemoryManager(project_root=tmp_path / "workspace")
    entry = repaired.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.category == "general"
    assert entry.approval_content_hash != digest
    assert entry.retrieval_count == 0
    assert entry.injection_count == 0
    assert entry.success_count == 0
    assert entry.feedback_observations == {}
    assert entry.last_accessed == 0.0


def test_exact_duplicate_tag_union_resets_surviving_version_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    master = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use isolated checkout fixtures.",
        tags=["pytest"],
        source="test",
        allow_duplicate=True,
    )
    duplicate = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use isolated checkout fixtures.",
        tags=["checkout"],
        source="test",
        allow_duplicate=True,
    )
    assert master is not None
    assert duplicate is not None
    old_version = (master.id, master.approval_content_hash)
    manager.record_retrievals_and_injections([master.id], [master.id])
    manager.record_feedback(
        [master.id],
        True,
        observation_id="run-before-exact-merge",
        expected_versions=[old_version],
    )

    result = manager.compress_scope(MemoryScope.PROJECT)

    assert result["removed_count"] == 1
    surviving = manager.memories[MemoryScope.PROJECT]._id_index[master.id]
    assert set(surviving.tags) == {"pytest", "checkout"}
    assert surviving.approval_content_hash != old_version[1]
    assert (
        surviving.approval_content_hash
        == memory_module._approval_hash_for_entry(surviving)
    )
    assert surviving.approval_status == "pending"
    assert surviving.retrieval_count == 0
    assert surviving.injection_count == 0
    assert surviving.success_count == 0
    assert surviving.feedback_observations == {}
    assert surviving.last_accessed == 0.0
    manager.record_feedback(
        [master.id],
        False,
        observation_id="delayed-old-version",
        expected_versions=[old_version],
    )
    assert surviving.failure_count == 0


@pytest.mark.parametrize(
    ("signal", "success_count", "failure_count"),
    [("accept", 1, 0), ("correct", 0, 1), ("reject", 0, 1)],
)
def test_whole_response_written_memory_feedback_is_version_bound_and_weak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal: str,
    success_count: int,
    failure_count: int,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    materialization = _entry(
        manager,
        "The completed checkout task established transactional fixtures.",
    )

    _apply_written_run_outcome_feedback(
        tmp_path / "workspace",
        _RunMemoryJournal((), written_versions=(materialization,)),
        f"run-written-{signal}",
        signal,
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[
        materialization[0]
    ]
    assert entry.success_count == success_count
    assert entry.failure_count == failure_count
    assert entry.corroborated_success_count == 0
    assert entry.corroborated_failure_count == 0
    assert entry.is_active is True


def test_delayed_written_memory_feedback_cannot_cross_content_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry_id, old_digest = _entry(
        manager,
        "OLD VERSION: the checkout task established isolated fixtures.",
    )
    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        "NEW VERSION: the checkout task established transactional fixtures.",
    )
    manager.approve_entry(
        entry_id,
        actor="test",
        reason="approve replacement",
    )

    _apply_written_run_outcome_feedback(
        tmp_path / "workspace",
        _RunMemoryJournal(
            (),
            written_versions=((entry_id, old_digest),),
        ),
        "run-written-old-version",
        "reject",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert entry.content.startswith("NEW VERSION")
    assert entry.failure_count == 0
    assert entry.corroborated_failure_count == 0
    assert entry.is_active is True


def test_explicit_memory_specific_verdict_remains_strong_and_subset_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    incorrect = _entry(manager, "Use the obsolete checkout fixture.")
    unrelated = _entry(manager, "Keep checkout currency in Decimal.")

    manager.record_corroborated_feedback(
        [incorrect[0]],
        False,
        source="explicit_user_correction",
        observation_id="memory-verdict-a",
        expected_versions=[incorrect],
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    rejected = current.memories[MemoryScope.PROJECT]._id_index[incorrect[0]]
    preserved = current.memories[MemoryScope.PROJECT]._id_index[unrelated[0]]
    assert rejected.corroborated_failure_count == 1
    assert rejected.is_active is False
    assert preserved.corroborated_failure_count == 0
    assert preserved.is_active is True


def test_automatic_and_explicit_run_outcome_use_distinct_delivery_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    materialization = _entry(manager, "Use isolated checkout fixtures.")
    manager.record_feedback(
        [materialization[0]],
        True,
        observation_id="run-two-channels",
        source="task_outcome",
        expected_versions=[materialization],
    )

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        _RunMemoryJournal((materialization,)),
        "run-two-channels",
        "correct",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[
        materialization[0]
    ]
    assert entry.success_count == 1
    assert entry.failure_count == 1
    assert entry.corroborated_failure_count == 0


def test_one_version_rendered_and_written_is_counted_once_per_run_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    materialization = _entry(manager, "Use isolated checkout fixtures.")
    journal = _RunMemoryJournal(
        (materialization,),
        written_versions=(materialization,),
    )

    _apply_run_outcome_memory_feedback(
        tmp_path / "workspace",
        journal,
        "run-input-and-output",
        "accept",
    )
    _apply_written_run_outcome_feedback(
        tmp_path / "workspace",
        journal,
        "run-input-and-output",
        "accept",
    )

    current = MemoryManager(project_root=tmp_path / "workspace")
    entry = current.memories[MemoryScope.PROJECT]._id_index[
        materialization[0]
    ]
    assert entry.success_count == 1
    assert entry.corroborated_success_count == 0
