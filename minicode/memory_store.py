"""Cooperative transaction lock for MiniCode's local Memory store.

The lock coordinates cooperating MiniCode processes on one local filesystem.
It is not a distributed lock and makes no claim for NFS or multi-host storage.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from minicode.advisory_lock import (
    acquire_advisory_lock,
    prepare_advisory_lock_file,
    release_advisory_lock,
)


class MemoryStoreError(RuntimeError):
    """Base class for fixed-vocabulary Memory store failures."""

    code = "memory_write_conflict"


class MemoryStoreBusy(MemoryStoreError):
    """The cooperative store lock was not acquired before its deadline."""

    code = "memory_store_busy"


class MemoryStoreConflict(MemoryStoreError):
    """A caller attempted to commit from a stale authority view."""

    code = "memory_write_conflict"


class MemoryStoreUnsafePath(MemoryStoreError):
    """A store root or file would redirect writes outside its owner.

    Raised for a symlinked scope root or store file, and for a
    Project/Local root whose parent is not the owning Workspace.
    """

    code = "memory_store_unsafe_path"


class MemoryStoreUnavailable(MemoryStoreError):
    """The local lock file could not be opened safely."""

    code = "memory_approval_unavailable"


@dataclass
class _ProcessLockState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    local: threading.local = field(default_factory=threading.local)


_REGISTRY_GUARD = threading.Lock()
_LOCK_REGISTRY: dict[str, _ProcessLockState] = {}


def _platform_name() -> str:
    return os.name


def _is_windows_reparse_point(metadata: os.stat_result) -> bool:
    if _platform_name() != "nt":
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _windows_path_has_reparse_component(path: Path) -> bool:
    if _platform_name() != "nt":
        return False
    absolute = Path(os.path.abspath(os.fspath(path)))
    components = (*reversed(absolute.parents), absolute)
    for component in components:
        metadata = os.lstat(component)
        if stat.S_ISLNK(metadata.st_mode) or _is_windows_reparse_point(metadata):
            return True
    return False


def _process_lock_state(path: Path) -> _ProcessLockState:
    key = os.path.abspath(os.fspath(path))
    with _REGISTRY_GUARD:
        return _LOCK_REGISTRY.setdefault(key, _ProcessLockState())


class MemoryStoreCoordinator:
    """Own the process/advisory-lock boundary for every durable Memory writer."""

    def __init__(self, root: str | Path, *, timeout: float = 5.0) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be numeric")
        if not 0 < float(timeout) <= 60:
            raise ValueError("timeout is out of range")
        self._root = Path(root)
        self._lock_path = self._root / "memory-store.lock"
        self._timeout = float(timeout)
        self._state = _process_lock_state(self._lock_path)

    @property
    def in_transaction(self) -> bool:
        return int(getattr(self._state.local, "depth", 0)) > 0

    def _open_lock_file(self) -> int:
        root_fd: int | None = None
        fd: int | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root_path_stat = os.lstat(self._root)
            if (
                not stat.S_ISDIR(root_path_stat.st_mode)
                or _windows_path_has_reparse_component(self._root)
            ):
                raise MemoryStoreUnavailable("unsafe Memory store root")
            if _platform_name() == "posix":
                root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                root_flags |= getattr(os, "O_CLOEXEC", 0)
                root_flags |= getattr(os, "O_NOFOLLOW", 0)
                root_fd = os.open(self._root, root_flags)
                root_fd_stat = os.fstat(root_fd)
                if (
                    not stat.S_ISDIR(root_fd_stat.st_mode)
                    or root_path_stat.st_dev != root_fd_stat.st_dev
                    or root_path_stat.st_ino != root_fd_stat.st_ino
                ):
                    raise MemoryStoreUnavailable("unsafe Memory store root")
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._lock_path, flags, 0o600)
            path_stat = os.lstat(self._lock_path)
            fd_stat = os.fstat(fd)
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or not stat.S_ISREG(fd_stat.st_mode)
                or _is_windows_reparse_point(path_stat)
                or path_stat.st_dev != fd_stat.st_dev
                or path_stat.st_ino != fd_stat.st_ino
            ):
                raise MemoryStoreUnavailable("unsafe Memory lock file")
            prepare_advisory_lock_file(fd, mode=0o600)
            return fd
        except MemoryStoreUnavailable:
            if fd is not None:
                os.close(fd)
            raise
        except OSError as error:
            if fd is not None:
                os.close(fd)
            raise MemoryStoreUnavailable("Memory lock file is unavailable") from error
        finally:
            if root_fd is not None:
                os.close(root_fd)

    @contextmanager
    def transaction(self, *, timeout: float | None = None) -> Iterator[None]:
        """Acquire the local and cross-process locks until the transaction ends."""
        wait_for = self._timeout if timeout is None else timeout
        if isinstance(wait_for, bool) or not isinstance(wait_for, (int, float)):
            raise TypeError("timeout must be numeric")
        wait_for = float(wait_for)
        if wait_for <= 0:
            raise MemoryStoreBusy("Memory store is busy")
        deadline = time.monotonic() + wait_for
        remaining = max(0.0, deadline - time.monotonic())
        if not self._state.lock.acquire(timeout=remaining):
            raise MemoryStoreBusy("Memory store is busy")

        depth = int(getattr(self._state.local, "depth", 0))
        if depth:
            self._state.local.depth = depth + 1
            try:
                yield
            finally:
                self._state.local.depth -= 1
                self._state.lock.release()
            return

        fd: int | None = None
        try:
            fd = self._open_lock_file()
            while True:
                try:
                    if acquire_advisory_lock(fd):
                        break
                except OSError as error:
                    raise MemoryStoreUnavailable("Memory store lock failed") from error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MemoryStoreBusy("Memory store is busy")
                time.sleep(min(0.01, remaining))
            self._state.local.depth = 1
            yield
        finally:
            self._state.local.depth = 0
            if fd is not None:
                try:
                    release_advisory_lock(fd)
                except OSError:
                    pass
                finally:
                    os.close(fd)
            self._state.lock.release()


__all__ = [
    "MemoryStoreBusy",
    "MemoryStoreConflict",
    "MemoryStoreCoordinator",
    "MemoryStoreError",
    "MemoryStoreUnavailable",
]
