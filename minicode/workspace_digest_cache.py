"""Process-lifetime content-digest cache for workspace tree digests.

A structured workflow hashes workspace trees roughly ten times (baseline,
freeze reconciliation, verification clones, promote validation).  Each walk
re-reads and re-hashes every file even though the overwhelmingly common case
is that the bytes never changed between two walks.

Reuse is limited to the same filesystem file identity and change metadata.
Copied files and different project roots are independently hashed, even when
relative paths, sizes and preserved timestamps match. Authoritative workflow
gates request a fresh read regardless of this performance cache.

``MINI_CODE_WORKSPACE_DIGEST_CACHE=0`` disables reuse without changing the
tree-digest encoding.
"""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

_CACHE_ENV_NAME = "MINI_CODE_WORKSPACE_DIGEST_CACHE"
_DEFAULT_ENTRY_LIMIT = 100_000
_CHUNK_BYTES = 1024 * 1024
_FileIdentity = tuple[str, int, int, int, int, int]


def _file_identity(path: Path) -> _FileIdentity:
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"workspace digest requires a regular file: {path}")
    return (
        str(path.resolve(strict=True)),
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _resolve_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    raw = source.get(_CACHE_ENV_NAME)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class WorkspaceDigestCache:
    """Bounded, thread-safe cache of per-file content digests."""

    def __init__(
        self,
        *,
        entry_limit: int = _DEFAULT_ENTRY_LIMIT,
        enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if isinstance(entry_limit, bool) or entry_limit < 1:
            raise ValueError("digest cache entry limit must be a positive int")
        self._entry_limit = entry_limit
        # ``enabled`` resolves lazily per access so a host can flip the env
        # kill-switch at runtime; an explicit constructor value wins.
        self._explicit_enabled = None if enabled is None else bool(enabled)
        self._env = env
        self._lock = threading.Lock()
        self._entries: dict[_FileIdentity, bytes] = {}
        self._hits = 0
        self._misses = 0

    @property
    def enabled(self) -> bool:
        if self._explicit_enabled is not None:
            return self._explicit_enabled
        return _resolve_enabled(self._env)

    @property
    def statistics(self) -> tuple[int, int]:
        """Return ``(hits, misses)`` for tests and diagnostics."""
        with self._lock:
            return (self._hits, self._misses)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def content_digest(
        self,
        path: Path,
        *,
        relative_posix: str,
        size: int,
        mtime_ns: int,
        checkpoint: Callable[[], None] | None = None,
        use_cache: bool = True,
    ) -> bytes:
        """Return the 32-byte SHA-256 of one regular file's content.

        Caller-supplied path/size/mtime hints are retained for compatibility;
        actual file identity and ctime determine reuse. A changed or missing
        file during hashing must never populate the cache or supply unstable
        evidence. Safety gates set ``use_cache=False`` to read actual bytes.
        """

        cache_enabled = use_cache and self.enabled
        key = _file_identity(path)
        if cache_enabled:
            cached = self._lookup(key)
            if cached is not None:
                return cached
        for attempt in range(2):
            digest = self._hash_file(path, checkpoint)
            final_key = _file_identity(path)
            if final_key == key:
                if cache_enabled:
                    self._store(final_key, digest)
                return digest
            if attempt == 1:
                raise OSError(f"workspace file changed while hashing: {path}")
            key = final_key
        raise AssertionError("unreachable digest retry loop")

    def _lookup(self, key: _FileIdentity) -> bytes | None:
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None:
            with self._lock:
                self._hits += 1
        return cached

    def _store(self, key: _FileIdentity, digest: bytes) -> None:
        with self._lock:
            self._misses += 1
            if len(self._entries) >= self._entry_limit:
                # FIFO eviction keeps the bound without a second structure.
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = digest

    @staticmethod
    def _hash_file(
        path: Path,
        checkpoint: Callable[[], None] | None,
    ) -> bytes:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                if checkpoint is not None:
                    checkpoint()
                digest.update(chunk)
        return digest.digest()


_WORKSPACE_DIGEST_CACHE = WorkspaceDigestCache()


def shared_workspace_digest_cache() -> WorkspaceDigestCache:
    """One process-lifetime cache shared by every workflow transaction."""

    return _WORKSPACE_DIGEST_CACHE


__all__ = [
    "WorkspaceDigestCache",
    "shared_workspace_digest_cache",
]
