"""Process-lifetime content-digest cache for workspace tree digests.

A structured workflow hashes workspace trees roughly ten times (baseline,
freeze reconciliation, verification clones, promote validation).  Each walk
re-reads and re-hashes every file even though the overwhelmingly common case
is that the bytes never changed between two walks.

This module caches the per-file content digest keyed by
``(relative_path, size, mtime_ns)``.  That key assumes content identity when
all three stat components match — the same assumption git's index makes to
skip hashing unchanged files.  Tree copies inside one workflow preserve
``mtime_ns`` via ``shutil.copystat``, so the cache also hits across the
snapshot -> frozen-container -> verification-clone chain, not only across
repeated walks of one tree.

The assumption is safe within the documented subprocess threat model (worker
runtime code is trusted; a same-UID process forging nanosecond-stat-identical
replacements is out of scope).  ``MINI_CODE_WORKSPACE_DIGEST_CACHE=0``
restores the legacy always-rehash behavior, including the legacy tree-digest
byte stream.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Callable

_CACHE_ENV_NAME = "MINI_CODE_WORKSPACE_DIGEST_CACHE"
_DEFAULT_ENTRY_LIMIT = 100_000
_CHUNK_BYTES = 1024 * 1024


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
        self._entries: dict[tuple[str, int, int], bytes] = {}
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
    ) -> bytes:
        """Return the 32-byte SHA-256 of one regular file's content.

        A mid-read modification would poison the cache entry, so the file's
        stat is re-checked after hashing and the read retried once when the
        file moved underneath the reader.
        """

        key = (relative_posix, size, mtime_ns)
        cached = self._lookup(key)
        if cached is not None:
            return cached
        for attempt in range(2):
            digest = self._hash_file(path, checkpoint)
            try:
                final_stat = path.stat(follow_symlinks=False)
            except OSError:
                final_stat = None
            if final_stat is None:
                # The file vanished mid-hash; the tree walk will fail on the
                # entry anyway.  Do not cache anything unreadable.
                return digest
            final_key = (
                relative_posix,
                final_stat.st_size,
                final_stat.st_mtime_ns,
            )
            if final_key == key or attempt == 1:
                if final_key == key:
                    self._store(final_key, digest)
                return digest
            # The stat moved underneath the reader: rehash against the new
            # state before deciding what to cache.
            key = final_key
        raise AssertionError("unreachable digest retry loop")

    def _lookup(self, key: tuple[str, int, int]) -> bytes | None:
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None:
            with self._lock:
                self._hits += 1
        return cached

    def _store(self, key: tuple[str, int, int], digest: bytes) -> None:
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
