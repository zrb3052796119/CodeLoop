"""Workspace authority must remain bound to bytes across preserved metadata."""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from minicode.workflow_workspace import (
    WorkflowIsolationError,
    WorkflowWorkspaceTransaction,
)
from minicode.workspace_digest_cache import shared_workspace_digest_cache


@pytest.fixture(autouse=True)
def enabled_clean_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MINI_CODE_WORKSPACE_DIGEST_CACHE", "1")
    shared_workspace_digest_cache().clear()
    yield
    shared_workspace_digest_cache().clear()


def _digest(root: Path) -> str:
    transaction = object.__new__(WorkflowWorkspaceTransaction)
    return transaction._filesystem_state_digest(root, use_operation_guard=False)


def test_unequal_project_files_with_matching_metadata_have_distinct_digests(
    tmp_path: Path,
) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "config.txt").write_text("allow=1\n", encoding="utf-8")
    (right / "config.txt").write_text("allow=0\n", encoding="utf-8")
    shutil.copystat(left / "config.txt", right / "config.txt")

    assert _digest(left) != _digest(right)


def test_same_file_byte_change_with_preserved_mtime_invalidates_cached_digest(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "repo"
    parent.mkdir()
    target = parent / "config.txt"
    target.write_text("allow=1\n", encoding="utf-8")
    archived = tmp_path / "archived.txt"
    shutil.copy2(target, archived)
    previous_digest = _digest(parent)
    target.write_text("allow=0\n", encoding="utf-8")
    shutil.copystat(archived, target)

    assert _digest(parent) != previous_digest


@pytest.mark.parametrize("write_patch", [True, False], ids=["write", "no_op"])
def test_timestamp_preserving_dependency_restore_invalidates_verified_promotion(
    tmp_path: Path, write_patch: bool,
) -> None:
    parent = tmp_path / "repo"
    parent.mkdir()
    (parent / "a.txt").write_text("0\n", encoding="utf-8")
    (parent / "b.txt").write_text("0\n", encoding="utf-8")
    restored = tmp_path / "archived_a.txt"
    restored.write_text("1\n" if write_patch else "2\n", encoding="utf-8")
    shutil.copystat(parent / "a.txt", restored)

    with WorkflowWorkspaceTransaction(
        str(parent), baseline_scope_paths=("b.txt",)
    ) as transaction:
        if write_patch:
            (transaction.cwd / "b.txt").write_text("1\n", encoding="utf-8")
        frozen = transaction.freeze(allowed_paths=("b.txt",))
        with frozen.verification_workspace() as verification:
            assert sum(
                int((verification / name).read_text(encoding="utf-8"))
                for name in ("a.txt", "b.txt")
            ) <= 1

        previous = (parent / "a.txt").stat()
        shutil.copy2(restored, parent / "a.txt")
        current = (parent / "a.txt").stat()
        assert (previous.st_size, previous.st_mtime_ns) == (
            current.st_size, current.st_mtime_ns
        )
        with pytest.raises(WorkflowIsolationError, match="parent workspace changed"):
            frozen.promote(approved_patch_digest=frozen.patch_digest)
        assert not transaction.parent_promotion_completed
        assert not transaction.parent_promotion_unknown
        assert (parent / "b.txt").read_text(encoding="utf-8") == "0\n"


@pytest.mark.parametrize("initial, final", [("1", "0"), ("0", "1")])
def test_cache_toggle_preserves_frozen_byte_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str, final: str,
) -> None:
    monkeypatch.setenv("MINI_CODE_WORKSPACE_DIGEST_CACHE", initial)
    (tmp_path / "target.txt").write_text("before", encoding="utf-8")
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        (transaction.cwd / "target.txt").write_text("after", encoding="utf-8")
        frozen = transaction.freeze(allowed_paths=("target.txt",))
        monkeypatch.setenv("MINI_CODE_WORKSPACE_DIGEST_CACHE", final)
        with frozen.verification_workspace() as verification:
            assert (verification / "target.txt").read_text(encoding="utf-8") == "after"
        frozen.promote(approved_patch_digest=frozen.patch_digest)
        assert transaction.parent_promotion_completed
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "after"
