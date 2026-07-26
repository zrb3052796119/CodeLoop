"""Cooperative transaction lock for MiniCode's local Memory store.

The lock coordinates cooperating MiniCode processes on macOS/Linux.  It is not
a distributed lock and makes no claim for Windows, NFS, or multi-host storage.
"""

from __future__ import annotations

import errno
import os
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

try:  # POSIX-only by design.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported hosts
    fcntl = None  # type: ignore[assignment]


class MemoryStoreError(RuntimeError):
    """Base class for fixed-vocabulary Memory store failures."""

    code = "memory_write_conflict"


class MemoryStoreBusy(MemoryStoreError):
    """The cooperative store lock was not acquired before its deadline."""

    code = "memory_store_busy"


class MemoryStoreConflict(MemoryStoreError):
    """A caller attempted to commit from a stale authority view."""

    code = "memory_write_conflict"


class MemoryStoreUnavailable(MemoryStoreError):
    """The local lock file could not be opened safely."""

    code = "memory_approval_unavailable"


@dataclass
class _ProcessLockState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    local: threading.local = field(default_factory=threading.local)


_REGISTRY_GUARD = threading.Lock()
_LOCK_REGISTRY: dict[str, _ProcessLockState] = {}


def _process_lock_state(path: Path) -> _ProcessLockState:
    key = os.path.abspath(os.fspath(path))
    with _REGISTRY_GUARD:
        return _LOCK_REGISTRY.setdefault(key, _ProcessLockState())


class MemoryStoreCoordinator:
    """Own the RLock -> flock boundary shared by every durable Memory writer."""

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
        if fcntl is None:
            raise MemoryStoreUnavailable("POSIX file locking is unavailable")
        root_fd: int | None = None
        fd: int | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            root_flags |= getattr(os, "O_CLOEXEC", 0)
            root_flags |= getattr(os, "O_NOFOLLOW", 0)
            root_fd = os.open(self._root, root_flags)
            root_path_stat = os.lstat(self._root)
            root_fd_stat = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_path_stat.st_mode)
                or not stat.S_ISDIR(root_fd_stat.st_mode)
                or root_path_stat.st_dev != root_fd_stat.st_dev
                or root_path_stat.st_ino != root_fd_stat.st_ino
            ):
                raise MemoryStoreUnavailable("unsafe Memory store root")
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._lock_path, flags, 0o600)
            os.fchmod(fd, 0o600)
            path_stat = os.lstat(self._lock_path)
            fd_stat = os.fstat(fd)
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or not stat.S_ISREG(fd_stat.st_mode)
                or path_stat.st_dev != fd_stat.st_dev
                or path_stat.st_ino != fd_stat.st_ino
            ):
                raise MemoryStoreUnavailable("unsafe Memory lock file")
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
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise MemoryStoreUnavailable("Memory store lock failed") from error
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MemoryStoreBusy("Memory store is busy") from error
                    time.sleep(min(0.01, remaining))
            self._state.local.depth = 1
            yield
        finally:
            self._state.local.depth = 0
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
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
