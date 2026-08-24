"""Small cross-platform advisory-lock backend for local durable stores.

Callers continue to own path validation, timeout policy, and public error
mapping.  This module only normalizes the platform-specific byte-range lock.
"""

from __future__ import annotations

import errno
import os

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable on POSIX
    msvcrt = None  # type: ignore[assignment]


WINDOWS_LOCK_SENTINEL = b"0"
_BUSY_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", errno.EACCES),
    }
)


def _platform_name() -> str:
    return os.name


def _unavailable() -> OSError:
    return OSError(errno.ENOSYS, "advisory locking is unavailable")


def prepare_advisory_lock_file(file_descriptor: int, *, mode: int = 0o600) -> None:
    """Prepare one open regular file for the current platform's lock API."""
    platform = _platform_name()
    if platform == "posix":
        if fcntl is None:
            raise _unavailable()
        os.fchmod(file_descriptor, mode)
        return
    if platform == "nt":
        if msvcrt is None:
            raise _unavailable()
        if os.fstat(file_descriptor).st_size == 0:
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            if os.write(file_descriptor, WINDOWS_LOCK_SENTINEL) != 1:
                raise OSError(errno.EIO, "advisory lock sentinel write failed")
            os.fsync(file_descriptor)
        return
    raise _unavailable()


def acquire_advisory_lock(file_descriptor: int) -> bool:
    """Try to acquire an exclusive lock without blocking.

    Returns ``False`` only for ordinary lock contention. Other operating-system
    failures remain ``OSError`` so each store can preserve its public error
    vocabulary. ``KeyboardInterrupt`` and ``SystemExit`` are never rewritten.
    """
    platform = _platform_name()
    try:
        if platform == "posix":
            if fcntl is None:
                raise _unavailable()
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif platform == "nt":
            if msvcrt is None:
                raise _unavailable()
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
        else:
            raise _unavailable()
    except BlockingIOError:
        # Some test doubles and platform wrappers omit ``errno`` while still
        # using Python's dedicated non-blocking contention exception.
        return False
    except OSError as error:
        if error.errno in _BUSY_ERRNOS:
            return False
        raise
    return True


def release_advisory_lock(file_descriptor: int) -> None:
    """Release an advisory lock previously acquired by this process."""
    platform = _platform_name()
    if platform == "posix":
        if fcntl is None:
            raise _unavailable()
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        return
    if platform == "nt":
        if msvcrt is None:
            raise _unavailable()
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        return
    raise _unavailable()


__all__ = [
    "WINDOWS_LOCK_SENTINEL",
    "acquire_advisory_lock",
    "prepare_advisory_lock_file",
    "release_advisory_lock",
]
