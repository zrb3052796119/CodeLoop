"""Cross-process transaction coordination for the local Session store.

The lock is advisory: every MiniCode Session writer must cooperate by entering
this transaction seam. It is intentionally scoped to one local filesystem and
does not provide distributed, NFS, lease, heartbeat, or ownership semantics.
"""

from __future__ import annotations

import math
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from minicode.advisory_lock import (
    acquire_advisory_lock,
    prepare_advisory_lock_file,
    release_advisory_lock,
)


SESSION_STORE_LOCK_NAME = "session-store.lock"
SESSION_STORE_LOCK_TIMEOUT_SECONDS = 5.0
SESSION_STORE_LOCK_POLL_SECONDS = 0.01


class SessionStoreError(RuntimeError):
    """Base low-information Session storage coordination error."""


class SessionStoreBusyError(SessionStoreError):
    """The Session store remained exclusively locked past its timeout."""


class SessionStoreLockError(SessionStoreError):
    """The Session store lock target could not be used safely."""


class SessionWriteConflictError(SessionStoreError):
    """The persisted Session revision changed after the caller loaded it."""


def session_store_lock_path(data_dir: str | Path) -> Path:
    """Return the dynamically resolved lock path for one configured data root."""
    return Path(data_dir) / SESSION_STORE_LOCK_NAME


def _close_lock_file(file_descriptor: int) -> None:
    try:
        os.close(file_descriptor)
    except OSError:
        pass


def _validate_lock_target(path: Path, file_descriptor: int) -> None:
    """Require the opened descriptor and path to name the same regular file."""
    try:
        descriptor_stat = os.fstat(file_descriptor)
        path_stat = os.lstat(path)
    except OSError as error:
        raise SessionStoreLockError("session store lock unavailable") from error
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or not stat.S_ISREG(path_stat.st_mode)
        or descriptor_stat.st_dev != path_stat.st_dev
        or descriptor_stat.st_ino != path_stat.st_ino
    ):
        raise SessionStoreLockError("session store lock unavailable")


def _open_lock_file(data_dir: str | Path) -> tuple[Path, int]:
    path = session_store_lock_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SessionStoreLockError("session store lock unavailable") from error

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise SessionStoreLockError("session store lock unavailable") from error
    try:
        _validate_lock_target(path, file_descriptor)
        prepare_advisory_lock_file(file_descriptor, mode=0o600)
    except BaseException:
        _close_lock_file(file_descriptor)
        raise
    return path, file_descriptor


def _acquire_lock(
    path: Path,
    file_descriptor: int,
    *,
    timeout: float,
    monotonic: Callable[[], float],
    wait: Callable[[float], None],
) -> None:
    deadline = monotonic() + timeout
    while True:
        try:
            if acquire_advisory_lock(file_descriptor):
                _validate_lock_target(path, file_descriptor)
                return
        except InterruptedError:
            pass
        except OSError as error:
            raise SessionStoreLockError("session store lock unavailable") from error
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise SessionStoreBusyError("session store is busy")
        wait(min(SESSION_STORE_LOCK_POLL_SECONDS, remaining))


def _release_lock(file_descriptor: int) -> None:
    try:
        release_advisory_lock(file_descriptor)
    except OSError:
        pass
    _close_lock_file(file_descriptor)


@contextmanager
def session_store_transaction(
    data_dir: str | Path,
    *,
    timeout: float | None = None,
    monotonic: Callable[[], float] | None = None,
    wait: Callable[[float], None] | None = None,
) -> Iterator[None]:
    """Hold one bounded exclusive advisory lock for a storage transaction."""
    timeout_value = (
        SESSION_STORE_LOCK_TIMEOUT_SECONDS if timeout is None else timeout
    )
    if (
        isinstance(timeout_value, bool)
        or not isinstance(timeout_value, (int, float))
        or not math.isfinite(float(timeout_value))
        or timeout_value < 0
    ):
        raise ValueError("invalid session store lock timeout")
    monotonic_clock = time.monotonic if monotonic is None else monotonic
    waiter = time.sleep if wait is None else wait
    path, file_descriptor = _open_lock_file(data_dir)
    acquired = False
    try:
        _acquire_lock(
            path,
            file_descriptor,
            timeout=float(timeout_value),
            monotonic=monotonic_clock,
            wait=waiter,
        )
        acquired = True
        yield
    finally:
        if acquired:
            _release_lock(file_descriptor)
        else:
            _close_lock_file(file_descriptor)
