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
    pid: int = field(default_factory=os.getpid)
    lock: threading.RLock = field(default_factory=threading.RLock)
    local: threading.local = field(default_factory=threading.local)
    active_fds: set[int] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _OpenedMemoryStoreLock:
    """Descriptors which bind one lock file to its verified store root."""

    lock_fd: int
    root_fd: int | None = None
    root_stat: os.stat_result | None = None
    lease_token: object = field(default_factory=object, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class MemoryStoreRootBinding:
    """Borrowed identity of a transaction's verified store root.

    ``root_fd`` remains owned by the coordinator and is valid only while the
    transaction is active. Publication code uses it for dirfd-relative writes
    so a renamed/recreated pathname cannot redirect authority bytes.
    """

    root: Path
    root_fd: int | None
    root_stat: os.stat_result
    _coordinator: "MemoryStoreCoordinator" = field(repr=False, compare=False)
    _lease_token: object = field(repr=False, compare=False)
    _pid: int = field(repr=False, compare=False)
    _thread_id: int = field(repr=False, compare=False)

    def assert_active(self) -> None:
        """Prove this borrowed binding still names its issuing transaction."""

        self._coordinator._assert_root_binding_active(self)


_REGISTRY_GUARD = threading.Lock()
_LOCK_REGISTRY: dict[str, _ProcessLockState] = {}


def _reset_lock_registry_after_fork() -> None:
    """Drop inherited thread locks and advisory-lock descriptors in a child."""

    global _REGISTRY_GUARD, _LOCK_REGISTRY
    inherited = tuple(_LOCK_REGISTRY.values())
    for state in inherited:
        for descriptor in tuple(state.active_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
    _REGISTRY_GUARD = threading.Lock()
    _LOCK_REGISTRY = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_lock_registry_after_fork)


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
    pid = os.getpid()
    with _REGISTRY_GUARD:
        state = _LOCK_REGISTRY.get(key)
        if state is None or state.pid != pid:
            state = _ProcessLockState(pid=pid)
            _LOCK_REGISTRY[key] = state
        return state


class MemoryStoreCoordinator:
    """Own the process/advisory-lock boundary for every durable Memory writer."""

    def __init__(
        self,
        root: str | Path,
        *,
        timeout: float = 5.0,
        lock_name: str = "memory-store.lock",
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be numeric")
        if not 0 < float(timeout) <= 60:
            raise ValueError("timeout is out of range")
        if (
            not isinstance(lock_name, str)
            or not lock_name
            or len(lock_name) > 128
            or lock_name in {".", ".."}
            or "/" in lock_name
            or "\\" in lock_name
            or "\x00" in lock_name
        ):
            raise ValueError("lock_name must be a safe basename")
        self._root = Path(root)
        self._lock_path = self._root / lock_name
        self._timeout = float(timeout)
        self._state = _process_lock_state(self._lock_path)

    def _state_for_current_process(self) -> _ProcessLockState:
        # POSIX fork inherits Python locks and thread-local depth, but the
        # child's copy does not own the parent's advisory-lock transaction.
        # Rebinding by PID prevents the nested fast-path from bypassing flock.
        if self._state.pid != os.getpid():
            self._state = _process_lock_state(self._lock_path)
        return self._state

    @property
    def in_transaction(self) -> bool:
        state = self._state_for_current_process()
        return int(getattr(state.local, "depth", 0)) > 0

    def assert_transaction_valid(self) -> None:
        """Revalidate the root and lock inode for the active transaction.

        The normal context-manager exit check detects a replaced store root,
        but that is too late for a caller about to publish authority bytes.
        Writers use this fence immediately before filesystem mutation so a
        renamed/recreated workspace fails closed instead of first writing into
        the replacement and only then reporting an error on lock release.
        """

        state = self._state_for_current_process()
        if int(getattr(state.local, "depth", 0)) <= 0:
            raise MemoryStoreConflict("Memory store transaction is not active")
        opened = getattr(state.local, "opened", None)
        if not isinstance(opened, _OpenedMemoryStoreLock):
            raise MemoryStoreConflict("Memory store transaction is not bound")
        self._verify_open_lock(opened)

    def transaction_root_binding(self) -> MemoryStoreRootBinding:
        """Return the active transaction's verified, borrowed root binding."""

        self.assert_transaction_valid()
        state = self._state_for_current_process()
        opened = getattr(state.local, "opened", None)
        if (
            not isinstance(opened, _OpenedMemoryStoreLock)
            or opened.root_stat is None
        ):
            raise MemoryStoreConflict("Memory store transaction is not bound")
        return MemoryStoreRootBinding(
            root=self._root,
            root_fd=opened.root_fd,
            root_stat=opened.root_stat,
            _coordinator=self,
            _lease_token=opened.lease_token,
            _pid=os.getpid(),
            _thread_id=threading.get_ident(),
        )

    def _assert_root_binding_active(
        self,
        binding: MemoryStoreRootBinding,
    ) -> None:
        """Validate an issued binding against the exact live lock lease."""

        if (
            binding._coordinator is not self
            or binding._pid != os.getpid()
            or binding._thread_id != threading.get_ident()
        ):
            raise MemoryStoreConflict("Memory store binding is not active")
        state = self._state_for_current_process()
        opened = getattr(state.local, "opened", None)
        if (
            int(getattr(state.local, "depth", 0)) <= 0
            or not isinstance(opened, _OpenedMemoryStoreLock)
            or opened.lease_token is not binding._lease_token
            or opened.root_fd != binding.root_fd
            or opened.root_stat is None
            or opened.root_stat.st_dev != binding.root_stat.st_dev
            or opened.root_stat.st_ino != binding.root_stat.st_ino
        ):
            raise MemoryStoreConflict("Memory store binding is not active")
        self._verify_open_lock(opened)

    def _verify_open_lock(self, opened: _OpenedMemoryStoreLock) -> None:
        """Revalidate the POSIX root and lock inode through their open fds."""

        if opened.root_stat is None:
            return
        if opened.root_fd is None:
            try:
                root_path_stat = os.lstat(self._root)
                lock_path_stat = os.lstat(self._lock_path)
                lock_fd_stat = os.fstat(opened.lock_fd)
            except OSError as error:
                raise MemoryStoreUnavailable("unsafe Memory store root") from error
            if (
                not stat.S_ISDIR(root_path_stat.st_mode)
                or _is_windows_reparse_point(root_path_stat)
                or root_path_stat.st_dev != opened.root_stat.st_dev
                or root_path_stat.st_ino != opened.root_stat.st_ino
                or not stat.S_ISREG(lock_path_stat.st_mode)
                or not stat.S_ISREG(lock_fd_stat.st_mode)
                or _is_windows_reparse_point(lock_path_stat)
                or lock_path_stat.st_dev != lock_fd_stat.st_dev
                or lock_path_stat.st_ino != lock_fd_stat.st_ino
            ):
                raise MemoryStoreUnavailable("unsafe Memory store root")
            return
        try:
            root_path_stat = os.lstat(self._root)
            root_fd_stat = os.fstat(opened.root_fd)
            lock_path_stat = os.stat(
                self._lock_path.name,
                dir_fd=opened.root_fd,
                follow_symlinks=False,
            )
            lock_fd_stat = os.fstat(opened.lock_fd)
        except OSError as error:
            raise MemoryStoreUnavailable("unsafe Memory store root") from error
        if (
            not stat.S_ISDIR(root_path_stat.st_mode)
            or not stat.S_ISDIR(root_fd_stat.st_mode)
            or root_path_stat.st_dev != root_fd_stat.st_dev
            or root_path_stat.st_ino != root_fd_stat.st_ino
            or opened.root_stat.st_dev != root_fd_stat.st_dev
            or opened.root_stat.st_ino != root_fd_stat.st_ino
            or not stat.S_ISREG(lock_path_stat.st_mode)
            or not stat.S_ISREG(lock_fd_stat.st_mode)
            or lock_path_stat.st_dev != lock_fd_stat.st_dev
            or lock_path_stat.st_ino != lock_fd_stat.st_ino
        ):
            raise MemoryStoreUnavailable("unsafe Memory store root")

    def _open_lock_file(self) -> _OpenedMemoryStoreLock:
        root_fd: int | None = None
        fd: int | None = None
        opened = False
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
            if root_fd is not None:
                for attempt in range(3):
                    try:
                        fd = os.open(
                            self._lock_path.name,
                            flags,
                            0o600,
                            dir_fd=root_fd,
                        )
                        break
                    except FileNotFoundError:
                        # macOS can transiently surface ENOENT when separate
                        # spawned processes concurrently create the nested
                        # store root and its first lock file.  Retry only while
                        # the pathname remains bound to this exact root fd;
                        # a root replacement is an unsafe-path failure, not a
                        # transient creation race.
                        current_root = os.lstat(self._root)
                        opened_root = os.fstat(root_fd)
                        if (
                            not stat.S_ISDIR(current_root.st_mode)
                            or not stat.S_ISDIR(opened_root.st_mode)
                            or current_root.st_dev != opened_root.st_dev
                            or current_root.st_ino != opened_root.st_ino
                            or attempt == 2
                        ):
                            raise MemoryStoreUnavailable(
                                "unsafe Memory store root"
                            )
                        time.sleep(0)
                if fd is None:  # defensive; the loop either opens or raises
                    raise MemoryStoreUnavailable(
                        "Memory lock file is unavailable"
                    )
                path_stat = os.stat(
                    self._lock_path.name,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
            else:
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
            result = _OpenedMemoryStoreLock(
                lock_fd=fd,
                root_fd=root_fd,
                root_stat=(
                    root_fd_stat if root_fd is not None else root_path_stat
                ),
            )
            self._verify_open_lock(result)
            opened = True
            return result
        except MemoryStoreUnavailable:
            raise
        except OSError as error:
            raise MemoryStoreUnavailable("Memory lock file is unavailable") from error
        finally:
            if not opened and fd is not None:
                os.close(fd)
            if not opened and root_fd is not None:
                os.close(root_fd)

    @contextmanager
    def transaction(self, *, timeout: float | None = None) -> Iterator[None]:
        """Acquire the local and cross-process locks until the transaction ends."""
        transaction_pid = os.getpid()
        wait_for = self._timeout if timeout is None else timeout
        if isinstance(wait_for, bool) or not isinstance(wait_for, (int, float)):
            raise TypeError("timeout must be numeric")
        wait_for = float(wait_for)
        if wait_for <= 0:
            raise MemoryStoreBusy("Memory store is busy")
        state = self._state_for_current_process()
        deadline = time.monotonic() + wait_for
        remaining = max(0.0, deadline - time.monotonic())
        if not state.lock.acquire(timeout=remaining):
            raise MemoryStoreBusy("Memory store is busy")

        depth = int(getattr(state.local, "depth", 0))
        if depth:
            state.local.depth = depth + 1
            try:
                yield
            finally:
                # A raw POSIX fork can unwind an inherited context manager in
                # the child.  The at-fork callback already discarded those
                # resources; only the process that acquired them may clean up.
                if os.getpid() == transaction_pid:
                    state.local.depth -= 1
                    state.lock.release()
            return

        opened: _OpenedMemoryStoreLock | None = None
        fd: int | None = None
        try:
            opened = self._open_lock_file()
            fd = opened.lock_fd
            state.active_fds.add(fd)
            if opened.root_fd is not None:
                state.active_fds.add(opened.root_fd)
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
            self._verify_open_lock(opened)
            state.local.depth = 1
            state.local.opened = opened
            yield
            if os.getpid() == transaction_pid:
                self._verify_open_lock(opened)
        finally:
            if os.getpid() == transaction_pid:
                state.local.depth = 0
                state.local.opened = None
                if fd is not None:
                    try:
                        release_advisory_lock(fd)
                    except OSError:
                        pass
                    finally:
                        os.close(fd)
                        state.active_fds.discard(fd)
                if opened is not None and opened.root_fd is not None:
                    os.close(opened.root_fd)
                    state.active_fds.discard(opened.root_fd)
                state.lock.release()


__all__ = [
    "MemoryStoreBusy",
    "MemoryStoreConflict",
    "MemoryStoreCoordinator",
    "MemoryStoreError",
    "MemoryStoreRootBinding",
    "MemoryStoreUnavailable",
]
