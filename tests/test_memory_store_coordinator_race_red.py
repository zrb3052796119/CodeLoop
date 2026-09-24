"""POSIX race contracts for the cooperative Memory store lock."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Literal

import pytest

import minicode.memory_store as memory_store_mod
from minicode.memory_store import MemoryStoreCoordinator, MemoryStoreUnavailable


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="directory-descriptor lock containment is a POSIX contract",
)


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


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


def _replace_root(
    root: Path,
    displaced: Path,
    decoy: Path,
    *,
    replacement_kind: Literal["symlink", "directory"],
) -> Path:
    root.rename(displaced)
    if replacement_kind == "symlink":
        root.symlink_to(decoy, target_is_directory=True)
        return decoy
    decoy.rename(root)
    return root


@pytest.mark.parametrize("replacement_kind", ["symlink", "directory"])
def test_root_replacement_after_directory_open_never_creates_a_decoy_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: Literal["symlink", "directory"],
) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    displaced = tmp_path / "data-root-original"
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    original_open = os.open
    swapped = False
    visible_decoy = decoy

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped, visible_decoy
        descriptor = _call_open(
            original_open,
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )
        path_text = os.fsdecode(os.fspath(path))
        if (
            not swapped
            and dir_fd is None
            and Path(os.path.abspath(path_text)) == root
            and bool(flags & getattr(os, "O_DIRECTORY", 0))
        ):
            visible_decoy = _replace_root(
                root,
                displaced,
                decoy,
                replacement_kind=replacement_kind,
            )
            swapped = True
        return descriptor

    monkeypatch.setattr(os, "open", racing_open)
    entered = False
    try:
        with MemoryStoreCoordinator(root).transaction():
            entered = True
    except MemoryStoreUnavailable:
        pass

    assert swapped
    assert entered is False
    assert not (visible_decoy / "memory-store.lock").exists()


def test_root_replacement_immediately_after_lock_acquisition_refuses_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    displaced = tmp_path / "data-root-original"
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    original_acquire = memory_store_mod.acquire_advisory_lock
    swapped = False

    def acquire_then_replace_root(descriptor: int) -> bool:
        nonlocal swapped
        acquired = original_acquire(descriptor)
        if acquired and not swapped:
            _replace_root(
                root,
                displaced,
                decoy,
                replacement_kind="symlink",
            )
            swapped = True
        return acquired

    monkeypatch.setattr(
        memory_store_mod,
        "acquire_advisory_lock",
        acquire_then_replace_root,
    )
    entered = False
    try:
        with MemoryStoreCoordinator(root).transaction():
            entered = True
    except MemoryStoreUnavailable:
        pass

    assert swapped
    assert entered is False
    assert not (decoy / "memory-store.lock").exists()


def test_normal_lock_is_opened_by_basename_under_the_bound_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    root_metadata = os.lstat(root)
    original_open = os.open
    original_acquire = memory_store_mod.acquire_advisory_lock
    lock_open: dict[str, object] = {}
    acquired_state: dict[str, os.stat_result] = {}

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = _call_open(
            original_open,
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )
        path_text = os.fsdecode(os.fspath(path))
        if (
            bool(flags & os.O_CREAT)
            and Path(path_text).name == "memory-store.lock"
        ):
            lock_open.update(
                {
                    "path": path_text,
                    "dir_fd": dir_fd,
                    "file": os.fstat(descriptor),
                    "root": os.fstat(dir_fd) if dir_fd is not None else None,
                }
            )
        return descriptor

    def recording_acquire(descriptor: int) -> bool:
        acquired = original_acquire(descriptor)
        if acquired:
            acquired_state["root"] = os.lstat(root)
            acquired_state["path"] = os.lstat(root / "memory-store.lock")
            acquired_state["file"] = os.fstat(descriptor)
        return acquired

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(
        memory_store_mod,
        "acquire_advisory_lock",
        recording_acquire,
    )

    with MemoryStoreCoordinator(root).transaction():
        assert _same_inode(root_metadata, os.lstat(root))
        assert _same_inode(
            os.lstat(root / "memory-store.lock"),
            acquired_state["file"],
        )

    assert lock_open["path"] == "memory-store.lock"
    assert isinstance(lock_open["dir_fd"], int)
    assert isinstance(lock_open["root"], os.stat_result)
    assert _same_inode(root_metadata, lock_open["root"])
    assert _same_inode(acquired_state["root"], root_metadata)
    assert _same_inode(acquired_state["path"], acquired_state["file"])
