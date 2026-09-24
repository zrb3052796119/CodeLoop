"""RED contracts for crash-durable, directory-contained Memory commits.

``memory.json`` is the canonical commit point for one Memory scope.  Atomic
rename alone prevents torn readers, but it does not make the acknowledged
bytes durable across a power loss, and path-based rename can be redirected if
the scope directory is replaced between validation and publication.

These tests deliberately exercise the public manager mutation path (and thus
the existing ``_atomic_write`` monkeypatch seam) while observing or faulting
only the operating-system publication boundary.  They do not prescribe a
publisher class or another production API.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable, Literal

import pytest

import minicode.memory as memory_mod
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_authority_publisher import MemoryScopePublication
from minicode.memory_blob_store import (
    MEMORY_BLOB_DIRECTORY_NAME,
    MemoryBlobStore,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="directory-descriptor authority publication is a POSIX contract",
)


class _SimulatedPowerLoss(BaseException):
    """Abrupt stop which normal application recovery must not swallow."""


def _manager_paths(tmp_path: Path) -> tuple[MemoryManager, Path, Path, Path]:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "data"
    workspace.mkdir(exist_ok=True)
    data_root.mkdir(exist_ok=True)
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    scope_root = workspace / ".mini-code-memory"
    return manager, workspace, data_root, scope_root


def _seed_authority(
    tmp_path: Path,
) -> tuple[MemoryManager, Path, Path, Path, str, str]:
    manager, workspace, data_root, scope_root = _manager_paths(tmp_path)
    old_content = "Keep the last fully durable parser rule."
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        old_content,
    )
    assert entry is not None
    return manager, workspace, data_root, scope_root, entry.id, old_content


def _same_inode(metadata: os.stat_result, descriptor: int) -> bool:
    opened = os.fstat(descriptor)
    return metadata.st_dev == opened.st_dev and metadata.st_ino == opened.st_ino


def _scope_child_for_descriptor(scope_root: Path, descriptor: int) -> str | None:
    """Return the name of a regular scope child currently held by ``fd``."""

    try:
        opened = os.fstat(descriptor)
    except OSError:
        return None
    if not stat.S_ISREG(opened.st_mode):
        return None
    try:
        children = tuple(os.scandir(scope_root))
    except OSError:
        return None
    for child in children:
        try:
            candidate = child.stat(follow_symlinks=False)
        except OSError:
            continue
        if (
            stat.S_ISREG(candidate.st_mode)
            and candidate.st_dev == opened.st_dev
            and candidate.st_ino == opened.st_ino
        ):
            return child.name
    return None


def _call_open(
    original: Callable[..., int],
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    flags: int,
    mode: int,
    *,
    dir_fd: int | None,
) -> int:
    if dir_fd is None:
        return original(path, flags, mode)
    return original(path, flags, mode, dir_fd=dir_fd)


def _call_replace(
    original: Callable[..., None],
    source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    *,
    src_dir_fd: int | None,
    dst_dir_fd: int | None,
) -> None:
    if src_dir_fd is None and dst_dir_fd is None:
        original(source, destination)
        return
    original(
        source,
        destination,
        src_dir_fd=src_dir_fd,
        dst_dir_fd=dst_dir_fd,
    )


def _call_mkdir(
    original: Callable[..., None],
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    mode: int,
    *,
    dir_fd: int | None,
) -> None:
    if dir_fd is None:
        original(path, mode)
        return
    original(path, mode, dir_fd=dir_fd)


def _is_authority_destination(
    destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    *,
    dst_dir_fd: int | None,
    memory_json: Path,
    scope_metadata: os.stat_result,
) -> bool:
    destination_text = os.fsdecode(os.fspath(destination))
    if dst_dir_fd is not None:
        return (
            destination_text == "memory.json"
            and _same_inode(scope_metadata, dst_dir_fd)
        )
    return Path(os.path.abspath(destination_text)) == memory_json


def test_canonical_commit_flushes_temp_then_replaces_relative_then_flushes_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _workspace, _data_root, scope_root, entry_id, _old = (
        _seed_authority(tmp_path)
    )
    memory_json = scope_root / "memory.json"
    scope_metadata = os.lstat(scope_root)
    original_fsync = os.fsync
    original_replace = os.replace
    sequence = 0
    synced_files: list[tuple[int, int, int, str]] = []
    parent_syncs: list[int] = []
    authority_replace: dict[str, object] = {}

    def recording_fsync(descriptor: int) -> None:
        nonlocal sequence
        sequence += 1
        opened = os.fstat(descriptor)
        child_name = _scope_child_for_descriptor(scope_root, descriptor)
        if child_name is not None:
            synced_files.append(
                (sequence, opened.st_dev, opened.st_ino, child_name)
            )
        elif stat.S_ISDIR(opened.st_mode) and _same_inode(
            scope_metadata, descriptor
        ):
            parent_syncs.append(sequence)
        original_fsync(descriptor)

    def recording_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal sequence
        is_authority = _is_authority_destination(
            destination,
            dst_dir_fd=dst_dir_fd,
            memory_json=memory_json,
            scope_metadata=scope_metadata,
        )
        if is_authority:
            sequence += 1
            source_text = os.fsdecode(os.fspath(source))
            if src_dir_fd is None:
                source_metadata = os.lstat(source)
            else:
                source_metadata = os.stat(
                    source_text,
                    dir_fd=src_dir_fd,
                    follow_symlinks=False,
                )
            authority_replace.update(
                {
                    "sequence": sequence,
                    "source": source_text,
                    "destination": os.fsdecode(os.fspath(destination)),
                    "src_dir_fd": src_dir_fd,
                    "dst_dir_fd": dst_dir_fd,
                    "src_dir_matches_scope": (
                        src_dir_fd is not None
                        and _same_inode(scope_metadata, src_dir_fd)
                    ),
                    "dst_dir_matches_scope": (
                        dst_dir_fd is not None
                        and _same_inode(scope_metadata, dst_dir_fd)
                    ),
                    "source_inode": (
                        source_metadata.st_dev,
                        source_metadata.st_ino,
                    ),
                }
            )
        _call_replace(
            original_replace,
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", recording_replace)

    assert manager.update_entry(
        MemoryScope.PROJECT,
        entry_id,
        "Publish the replacement parser rule durably.",
    )

    assert authority_replace, "memory.json was not replaced through the commit seam"
    replace_sequence = int(authority_replace["sequence"])
    source_inode = authority_replace["source_inode"]
    assert isinstance(source_inode, tuple)
    matching_temp_syncs = [
        sync_sequence
        for sync_sequence, device, inode, child_name in synced_files
        if (device, inode) == source_inode
        and child_name != "memory.json"
        and sync_sequence < replace_sequence
    ]
    assert matching_temp_syncs, "the exact replacement temp file was not fsynced"

    src_dir_fd = authority_replace["src_dir_fd"]
    dst_dir_fd = authority_replace["dst_dir_fd"]
    assert isinstance(src_dir_fd, int) and isinstance(dst_dir_fd, int)
    assert authority_replace["src_dir_matches_scope"] is True
    assert authority_replace["dst_dir_matches_scope"] is True
    assert authority_replace["destination"] == "memory.json"
    source_name = str(authority_replace["source"])
    assert source_name == Path(source_name).name

    matching_parent_syncs = [
        sync_sequence
        for sync_sequence in parent_syncs
        if sync_sequence > replace_sequence
    ]
    assert matching_parent_syncs, "the containing directory was not fsynced"
    assert max(matching_temp_syncs) < replace_sequence < min(
        matching_parent_syncs
    )


def test_first_scope_creation_flushes_workspace_before_add_entry_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, workspace, _data_root, scope_root = _manager_paths(tmp_path)
    assert not scope_root.exists()
    workspace_metadata = os.lstat(workspace)
    original_mkdir = os.mkdir
    original_fsync = os.fsync
    sequence = 0
    scope_created_at: int | None = None
    owner_synced_at: list[int] = []

    def recording_mkdir(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal scope_created_at, sequence
        _call_mkdir(original_mkdir, path, mode, dir_fd=dir_fd)
        path_text = os.fsdecode(os.fspath(path))
        is_scope_root = (
            dir_fd is not None
            and path_text == scope_root.name
            and _same_inode(workspace_metadata, dir_fd)
        ) or (
            dir_fd is None
            and Path(os.path.abspath(path_text)) == scope_root
        )
        if is_scope_root:
            sequence += 1
            scope_created_at = sequence

    def recording_fsync(descriptor: int) -> None:
        nonlocal sequence
        opened = os.fstat(descriptor)
        sequence += 1
        if stat.S_ISDIR(opened.st_mode) and _same_inode(
            workspace_metadata, descriptor
        ):
            owner_synced_at.append(sequence)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "mkdir", recording_mkdir)
    monkeypatch.setattr(os, "fsync", recording_fsync)

    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Persist the first project Memory root with its owner directory.",
    )

    assert entry is not None
    assert scope_created_at is not None
    assert owner_synced_at
    assert scope_created_at < min(owner_synced_at)


def test_first_blob_directory_creation_flushes_scope_root_before_put_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_root = tmp_path / "scope"
    scope_root.mkdir()
    scope_metadata = os.lstat(scope_root)
    store = MemoryBlobStore(scope_root)
    assert not store.root.exists()
    original_mkdir = os.mkdir
    original_fsync = os.fsync
    sequence = 0
    blob_directory_created_at: int | None = None
    scope_synced_at: list[int] = []

    def recording_mkdir(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal blob_directory_created_at, sequence
        _call_mkdir(original_mkdir, path, mode, dir_fd=dir_fd)
        path_text = os.fsdecode(os.fspath(path))
        if (
            path_text == MEMORY_BLOB_DIRECTORY_NAME
            and dir_fd is not None
            and _same_inode(scope_metadata, dir_fd)
        ):
            sequence += 1
            blob_directory_created_at = sequence

    def recording_fsync(descriptor: int) -> None:
        nonlocal sequence
        opened = os.fstat(descriptor)
        sequence += 1
        if stat.S_ISDIR(opened.st_mode) and _same_inode(
            scope_metadata, descriptor
        ):
            scope_synced_at.append(sequence)
        original_fsync(descriptor)

    monkeypatch.setattr(os, "mkdir", recording_mkdir)
    monkeypatch.setattr(os, "fsync", recording_fsync)

    reference = store.put_json({"first": "durable blob directory"})

    assert store.read_json(reference) == {"first": "durable blob directory"}
    assert blob_directory_created_at is not None
    assert scope_synced_at
    assert blob_directory_created_at < min(scope_synced_at)


@pytest.mark.parametrize(
    ("failure_stage", "expected_content"),
    [
        ("temp_fsync", "old"),
        ("replace", "old"),
        ("parent_fsync", "new"),
    ],
)
def test_stage_failure_leaves_one_complete_authority_for_a_fresh_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: Literal["temp_fsync", "replace", "parent_fsync"],
    expected_content: Literal["old", "new"],
) -> None:
    manager, workspace, data_root, scope_root, entry_id, old_content = (
        _seed_authority(tmp_path)
    )
    memory_json = scope_root / "memory.json"
    scope_metadata = os.lstat(scope_root)
    new_content = "Use the newly durable parser rule."
    original_fsync = os.fsync
    original_replace = os.replace
    replacement_completed = False
    injected = False

    def crashing_fsync(descriptor: int) -> None:
        nonlocal injected
        child_name = _scope_child_for_descriptor(scope_root, descriptor)
        is_scope_directory = (
            stat.S_ISDIR(os.fstat(descriptor).st_mode)
            and _same_inode(scope_metadata, descriptor)
        )
        if not injected and (
            (failure_stage == "temp_fsync" and child_name is not None)
            or (
                failure_stage == "parent_fsync"
                and replacement_completed
                and is_scope_directory
            )
        ):
            injected = True
            raise _SimulatedPowerLoss(failure_stage)
        original_fsync(descriptor)

    def crashing_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal injected, replacement_completed
        is_authority = _is_authority_destination(
            destination,
            dst_dir_fd=dst_dir_fd,
            memory_json=memory_json,
            scope_metadata=scope_metadata,
        )
        if is_authority and failure_stage == "replace" and not injected:
            injected = True
            raise _SimulatedPowerLoss(failure_stage)
        _call_replace(
            original_replace,
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if is_authority:
            replacement_completed = True

    monkeypatch.setattr(os, "fsync", crashing_fsync)
    monkeypatch.setattr(os, "replace", crashing_replace)

    crashed = False
    try:
        manager.update_entry(MemoryScope.PROJECT, entry_id, new_content)
    except _SimulatedPowerLoss:
        crashed = True

    # Only disk state observed by a new process counts.  The interrupted
    # manager may have staged mutable state which its transaction later rolls
    # back, and therefore is not a valid crash-recovery oracle.
    fresh = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    persisted = fresh.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert len(fresh.memories[MemoryScope.PROJECT].entries) == 1
    assert persisted.content in {old_content, new_content}
    assert persisted.content == (
        old_content if expected_content == "old" else new_content
    )
    assert crashed and injected, f"failure hook {failure_stage!r} was not reached"


@pytest.mark.parametrize("replacement_kind", ["symlink", "directory"])
def test_scope_root_replacement_during_publish_cannot_modify_the_decoy_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: Literal["symlink", "directory"],
) -> None:
    manager, _workspace, _data_root, scope_root, entry_id, _old_content = (
        _seed_authority(tmp_path)
    )
    displaced_root = scope_root.with_name(f"{scope_root.name}.original")
    decoy_root = tmp_path / "external-decoy"
    decoy_root.mkdir()
    decoy_authority = decoy_root / "memory.json"
    sentinel = b'outside authority must remain byte-for-byte untouched\n'
    decoy_authority.write_bytes(sentinel)
    original_publish = MemoryScopePublication.publish_text
    swapped = False

    def swap_scope_root() -> None:
        nonlocal swapped
        if swapped:
            return
        scope_root.rename(displaced_root)
        if replacement_kind == "symlink":
            scope_root.symlink_to(decoy_root, target_is_directory=True)
        else:
            decoy_root.rename(scope_root)
        swapped = True

    def racing_publish(
        publication: MemoryScopePublication,
        target: str | Path,
        content: str,
    ) -> None:
        if Path(target).name == "memory.json" and not swapped:
            # The scope has already been opened from the transaction root.
            # Replacing its pathname now must make the session fail closed.
            swap_scope_root()
        original_publish(publication, target, content)

    monkeypatch.setattr(MemoryScopePublication, "publish_text", racing_publish)

    try:
        manager.update_entry(
            MemoryScope.PROJECT,
            entry_id,
            "This update may only publish inside the anchored original root.",
        )
    except (OSError, memory_mod.MemoryStoreError):
        # Refusing the raced commit is valid; publishing against the already
        # opened original directory is valid too.  Writing the decoy is not.
        pass

    assert swapped, "the scope-root replacement race was not exercised"
    visible_decoy = (
        decoy_root / "memory.json"
        if replacement_kind == "symlink"
        else scope_root / "memory.json"
    )
    assert visible_decoy.read_bytes() == sentinel


def test_closed_temp_name_cannot_be_substituted_before_authority_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A random basename is not authority; its still-open inode is.

    If a publisher closes its private file before rename, another same-UID
    process can move that name aside and create a different regular file under
    it.  Holding the descriptor through replace removes that close-to-rename
    window; an implementation which intentionally closes early must otherwise
    detect the inode substitution and refuse it.
    """

    manager, workspace, data_root, scope_root, entry_id, old_content = (
        _seed_authority(tmp_path)
    )
    memory_json = scope_root / "memory.json"
    scope_metadata = os.lstat(scope_root)
    new_content = "Publish only the bytes bound to the fsynced descriptor."
    substituted = b'{"scope":"project","entries":[],"substituted":true}'
    original_open = os.open
    original_replace = os.replace
    original_stat = os.stat
    substitution_performed = False

    def substitute_name(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        directory_fd: int | None,
    ) -> None:
        nonlocal substitution_performed
        if substitution_performed:
            return
        source_text = os.fsdecode(os.fspath(source))
        displaced = f"{source_text}.displaced"
        _call_replace(
            original_replace,
            source,
            displaced,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        replacement_fd = original_open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            **({"dir_fd": directory_fd} if directory_fd is not None else {}),
        )
        try:
            position = 0
            while position < len(substituted):
                position += os.write(replacement_fd, substituted[position:])
            os.fsync(replacement_fd)
        finally:
            os.close(replacement_fd)
        substitution_performed = True

    def substituting_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        path_text = os.fsdecode(os.fspath(path))
        if (
            not substitution_performed
            and dir_fd is not None
            and _same_inode(scope_metadata, dir_fd)
            and path_text.startswith(".memory.json.")
            and path_text.endswith(".tmp")
        ):
            # Substitute immediately before a safe publisher binds the path
            # back to its still-open temp descriptor.  It must reject before
            # rename.  A legacy writer lacking this check is attacked below at
            # its replace syscall instead.
            substitute_name(path, directory_fd=dir_fd)
        return original_stat(
            path,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def tracking_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        is_authority = _is_authority_destination(
            destination,
            dst_dir_fd=dst_dir_fd,
            memory_json=memory_json,
            scope_metadata=scope_metadata,
        )
        if is_authority and not substitution_performed:
            substitute_name(source, directory_fd=src_dir_fd)
        _call_replace(
            original_replace,
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "stat", substituting_stat)
    monkeypatch.setattr(os, "replace", tracking_replace)

    try:
        manager.update_entry(MemoryScope.PROJECT, entry_id, new_content)
    except (OSError, memory_mod.MemoryStoreError):
        pass

    assert substitution_performed, "the temp-name substitution was not exercised"
    assert memory_json.read_bytes() != substituted
    fresh = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    persisted = fresh.memories[MemoryScope.PROJECT]._id_index[entry_id]
    assert persisted.content in {old_content, new_content}
    if substitution_performed:
        assert persisted.content == old_content
