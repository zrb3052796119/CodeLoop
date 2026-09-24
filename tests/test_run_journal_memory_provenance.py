from __future__ import annotations

import json
from pathlib import Path

import pytest

from minicode.memory_provenance import MemoryProvenanceBatch
from minicode.run_journal import (
    RunJournal,
    RunJournalMemoryProvenanceConflictError,
    RunJournalOwnershipError,
    RunJournalStorageError,
    RunJournalValidationError,
)

ENTRY_A = "project-1785082406796413000-b6ecf281"
ENTRY_B = "user-1785082406796413001-a1b2c3d4"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
SUBAGENT_ID = "sub_" + "c" * 32


def _journal(tmp_path: Path) -> tuple[RunJournal, str, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="Memory provenance", source="gateway")
    journal.transition(run.id, "running")
    run_dir = next(data_dir.rglob(run.id))
    return journal, run.id, run_dir


def _version(index: int) -> tuple[str, str]:
    return (
        f"project-{1785082406796413000 + index}-{index:08x}",
        f"{index:064x}",
    )


def _batch(
    *,
    effect: str = "rendered",
    origin: str = "task_start",
    versions: tuple[tuple[str, str], ...] = ((ENTRY_A, DIGEST_A),),
    receipt: str | None = "d" * 32,
    context: str | None = None,
    coverage: str = "complete",
) -> MemoryProvenanceBatch:
    return MemoryProvenanceBatch(
        effect=effect,
        origin=origin,
        authority_versions=versions,
        receipt_id=receipt,
        context_operation_id=context,
        coverage=coverage,
    )


def test_v1_sidecar_is_weak_and_migrates_without_fabricated_provenance(
    tmp_path: Path,
) -> None:
    journal, run_id, run_dir = _journal(tmp_path)
    legacy = {
        "schemaVersion": 1,
        "digestSchema": "memory-approval-content-sha256-v1",
        "kind": "rendered",
        "materializations": [
            {"entryId": ENTRY_A, "authorityDigest": DIGEST_A},
        ],
    }
    sidecar = run_dir / "memory_rendered_versions.json"
    sidecar.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    assert journal.get_rendered_memory_versions(run_id) == ((ENTRY_A, DIGEST_A),)
    legacy_snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert legacy_snapshot.materializations == ((ENTRY_A, DIGEST_A),)
    assert legacy_snapshot.records == ()
    assert legacy_snapshot.coverage == "partial"

    journal.record_rendered_memory_versions(run_id, [(ENTRY_B, DIGEST_B)])

    migrated = json.loads(sidecar.read_text(encoding="utf-8"))
    assert migrated["schemaVersion"] == 2
    assert migrated["provenance"] == []
    assert migrated["coverage"] == "partial"
    assert journal.get_rendered_memory_versions(run_id) == (
        (ENTRY_A, DIGEST_A),
        (ENTRY_B, DIGEST_B),
    )


def test_root_and_subagent_records_round_trip_content_free(
    tmp_path: Path,
) -> None:
    journal, run_id, run_dir = _journal(tmp_path)
    root_batch = _batch()
    child_batch = _batch(
        origin="context_compaction",
        versions=((ENTRY_B, DIGEST_B),),
        receipt=None,
        context="ctxop_" + "e" * 32,
    )

    journal.record_memory_provenance(run_id, root_batch)
    journal.record_memory_provenance(
        run_id,
        child_batch,
        actor_kind="subagent",
        subagent_id=SUBAGENT_ID,
    )

    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert snapshot.coverage == "complete"
    assert snapshot.materializations == (
        (ENTRY_A, DIGEST_A),
        (ENTRY_B, DIGEST_B),
    )
    assert snapshot.records[0].actor.kind == "run_root"
    assert snapshot.records[0].actor.subagent_id is None
    assert snapshot.records[0].batch == root_batch
    assert snapshot.records[1].actor.kind == "subagent"
    assert snapshot.records[1].actor.subagent_id == SUBAGENT_ID
    assert snapshot.records[1].batch == child_batch

    persisted = json.loads(
        (run_dir / "memory_rendered_versions.json").read_text(
            encoding="utf-8"
        )
    )
    for record in persisted["provenance"]:
        assert set(record) == {"actor", "batch"}
        assert not ({"prompt", "query", "path", "content"} & set(record["batch"]))


@pytest.mark.parametrize(
    ("actor_kind", "subagent_id"),
    [
        ("run_root", SUBAGENT_ID),
        ("subagent", None),
        ("subagent", "sub_bad"),
        ("worker", None),
    ],
)
def test_actor_binding_is_strict(
    tmp_path: Path,
    actor_kind: str,
    subagent_id: str | None,
) -> None:
    journal, run_id, _run_dir = _journal(tmp_path)

    with pytest.raises(RunJournalValidationError):
        journal.record_memory_provenance(
            run_id,
            _batch(),
            actor_kind=actor_kind,
            subagent_id=subagent_id,
        )


@pytest.mark.parametrize("identity_kind", ["receipt", "context"])
def test_exact_replay_is_idempotent_but_same_actor_identity_conflicts(
    tmp_path: Path,
    identity_kind: str,
) -> None:
    journal, run_id, _run_dir = _journal(tmp_path)
    identity = "f" * 32
    kwargs = (
        {"receipt": identity, "context": None}
        if identity_kind == "receipt"
        else {"receipt": None, "context": "ctxop_" + identity}
    )
    original = _batch(**kwargs)
    conflicting = _batch(versions=((ENTRY_B, DIGEST_B),), **kwargs)

    journal.record_memory_provenance(run_id, original)
    journal.record_memory_provenance(run_id, original)
    assert len(journal.get_memory_provenance(run_id, "rendered").records) == 1

    with pytest.raises(RunJournalMemoryProvenanceConflictError):
        journal.record_memory_provenance(run_id, conflicting)

    # Identity is actor-scoped: a child may legitimately report its own copy.
    journal.record_memory_provenance(
        run_id,
        conflicting,
        actor_kind="subagent",
        subagent_id=SUBAGENT_ID,
    )
    assert len(journal.get_memory_provenance(run_id, "rendered").records) == 2


def test_pair_overflow_atomically_keeps_capacity_and_marks_partial(
    tmp_path: Path,
) -> None:
    journal, run_id, _run_dir = _journal(tmp_path)
    for batch_index in range(7):
        start = batch_index * 64
        journal.record_memory_provenance(
            run_id,
            _batch(
                versions=tuple(_version(index) for index in range(start, start + 64)),
                receipt=f"{batch_index + 1:032x}",
            ),
        )
    journal.record_memory_provenance(
        run_id,
        _batch(
            versions=tuple(_version(index) for index in range(448, 511)),
            receipt=f"{8:032x}",
        ),
    )
    overflow = _batch(
        versions=(_version(511), _version(512)),
        receipt=f"{9:032x}",
    )

    journal.record_memory_provenance(run_id, overflow)

    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert len(snapshot.materializations) == 512
    assert snapshot.materializations[-1] == _version(511)
    assert _version(512) not in snapshot.materializations
    assert len(snapshot.records) == 9
    assert snapshot.records[-1].batch.authority_versions == (_version(511),)
    assert snapshot.records[-1].batch.coverage == "partial"
    assert snapshot.coverage == "partial"

    # Retrying the same over-capacity observation is still idempotent even
    # though only its representable prefix could be retained.
    journal.record_memory_provenance(run_id, overflow)
    assert journal.get_memory_provenance(run_id, "rendered") == snapshot

    with pytest.raises(RunJournalMemoryProvenanceConflictError):
        journal.record_memory_provenance(
            run_id,
            _batch(
                versions=(_version(510), _version(512)),
                receipt=f"{9:032x}",
            ),
        )


def test_compatibility_version_writer_caps_projection_without_raising(
    tmp_path: Path,
) -> None:
    journal, run_id, _run_dir = _journal(tmp_path)
    for batch_index in range(8):
        start = batch_index * 64
        journal.record_written_memory_versions(
            run_id,
            [_version(index) for index in range(start, start + 64)],
        )

    journal.record_written_memory_versions(run_id, [_version(512)])

    snapshot = journal.get_memory_provenance(run_id, "written")
    assert len(snapshot.materializations) == 512
    assert _version(512) not in snapshot.materializations
    assert snapshot.records == ()
    assert snapshot.coverage == "partial"


def test_record_overflow_is_persisted_as_partial_coverage(tmp_path: Path) -> None:
    journal, run_id, _run_dir = _journal(tmp_path)
    for index in range(128):
        journal.record_memory_provenance(
            run_id,
            _batch(
                versions=(),
                receipt=f"{index + 1:032x}",
                coverage="unknown",
            ),
        )

    journal.record_memory_provenance(
        run_id,
        _batch(
            versions=(),
            receipt="f" * 32,
            coverage="unknown",
        ),
    )

    snapshot = journal.get_memory_provenance(run_id, "rendered")
    assert len(snapshot.records) == 128
    assert snapshot.materializations == ()
    assert snapshot.coverage == "partial"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["provenance"][0]["actor"].update(
            {"subagentId": SUBAGENT_ID}
        ),
        lambda payload: payload["provenance"][0]["batch"].update(
            {"content": "must never be stored"}
        ),
        lambda payload: payload.update({"kind": "written"}),
        lambda payload: payload["materializations"].append(
            dict(payload["materializations"][0])
        ),
    ],
)
def test_malformed_v2_sidecar_fails_closed(
    tmp_path: Path,
    mutation,
) -> None:
    journal, run_id, run_dir = _journal(tmp_path)
    journal.record_memory_provenance(run_id, _batch())
    sidecar = run_dir / "memory_rendered_versions.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    mutation(payload)
    sidecar.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(RunJournalStorageError):
        journal.get_memory_provenance(run_id, "rendered")


def test_rendered_and_written_provenance_are_isolated(tmp_path: Path) -> None:
    journal, run_id, _run_dir = _journal(tmp_path)
    rendered = _batch()
    written = _batch(
        effect="written",
        origin="reflection",
        versions=((ENTRY_B, DIGEST_B),),
        receipt="e" * 32,
    )

    journal.record_memory_provenance(run_id, rendered)
    journal.record_memory_provenance(
        run_id,
        written,
        actor_kind="subagent",
        subagent_id=SUBAGENT_ID,
    )

    rendered_snapshot = journal.get_memory_provenance(run_id, "rendered")
    written_snapshot = journal.get_memory_provenance(run_id, "written")
    assert rendered_snapshot.materializations == rendered.authority_versions
    assert tuple(record.batch for record in rendered_snapshot.records) == (rendered,)
    assert written_snapshot.materializations == written.authority_versions
    assert tuple(record.batch for record in written_snapshot.records) == (written,)
    assert journal.get_rendered_memory_versions(run_id) == rendered.authority_versions
    assert journal.get_written_memory_versions(run_id) == written.authority_versions


def test_legacy_written_memory_version_survives_run_journal_round_trip(
    tmp_path: Path,
) -> None:
    journal, run_id, run_dir = _journal(tmp_path)
    legacy_version = ("user-1", DIGEST_A)
    written = _batch(
        effect="written",
        origin="reflection",
        versions=(legacy_version,),
        receipt="1" * 32,
    )

    journal.record_memory_provenance(run_id, written)

    snapshot = journal.get_memory_provenance(run_id, "written")
    assert snapshot.materializations == (legacy_version,)
    assert tuple(record.batch for record in snapshot.records) == (written,)
    persisted = json.loads(
        (run_dir / "memory_written_versions.json").read_text(encoding="utf-8")
    )
    assert persisted["materializations"] == [
        {"entryId": "user-1", "authorityDigest": DIGEST_A}
    ]


def test_provenance_preserves_terminal_ownership_and_symlink_guards(
    tmp_path: Path,
) -> None:
    journal, run_id, run_dir = _journal(tmp_path)
    reopened = RunJournal(journal.workspace, data_dir=journal.data_dir)
    with pytest.raises(RunJournalOwnershipError):
        reopened.record_memory_provenance(run_id, _batch())

    outside = tmp_path / "outside.json"
    outside.write_text('{"preserve":true}\n', encoding="utf-8")
    sidecar = run_dir / "memory_rendered_versions.json"
    sidecar.symlink_to(outside)
    with pytest.raises(RunJournalStorageError):
        journal.record_memory_provenance(run_id, _batch())
    assert sidecar.is_symlink()
    assert outside.read_text(encoding="utf-8") == '{"preserve":true}\n'

    sidecar.unlink()
    journal.transition(run_id, "completed")
    # Terminal transition releases the writer, preserving the pre-existing
    # RunJournal rule that ownership fails before the terminal-state check.
    with pytest.raises(RunJournalOwnershipError):
        journal.record_memory_provenance(run_id, _batch())
