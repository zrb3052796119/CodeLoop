"""Immutable, content-addressed JSON blobs for Memory authority auxiliaries.

The primary ``memory.json`` authority should contain only bounded references to
large, immutable auxiliary records.  This module owns those auxiliary bytes:
each blob is canonical strict JSON, addressed by its lowercase SHA-256 digest,
and published with create-if-absent semantics below one fixed scope directory.

This is deliberately not a garbage collector.  Removing a referenced blob can
make an otherwise valid authority impossible to replay, so deletion belongs to
a future, authority-aware retention protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from minicode.memory_authority_io import (
    MemoryAuthorityLimitError,
    parse_memory_authority_json,
    validate_memory_authority_value,
)
from minicode.memory_authority_publisher import MemoryAuthorityPublisher
from minicode.memory_store import (
    MemoryStoreConflict,
    MemoryStoreUnavailable,
    MemoryStoreUnsafePath,
)

if TYPE_CHECKING:
    from minicode.memory_authority_publisher import MemoryScopePublication

MAX_MEMORY_BLOB_BYTES = 64 * 1024
MEMORY_BLOB_DIRECTORY_NAME = "authority-blobs-v1"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_FIELDS = frozenset({"digest", "sizeBytes"})


class MemoryBlobMissingError(MemoryStoreConflict):
    """A requested immutable blob or its not-yet-created store is absent."""


@dataclass(frozen=True, slots=True)
class MemoryBlobReference:
    """A bounded reference to one immutable canonical JSON blob."""

    digest: str
    size_bytes: int

    def __post_init__(self) -> None:
        _require_digest(self.digest)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or not 0 < self.size_bytes <= MAX_MEMORY_BLOB_BYTES
        ):
            raise ValueError("Memory blob size is invalid")

    @property
    def filename(self) -> str:
        """Return the only valid on-disk basename for this reference."""

        return f"{self.digest}.json"

    def to_dict(self) -> dict[str, object]:
        """Return the compact closed reference document."""

        return {"digest": self.digest, "sizeBytes": self.size_bytes}

    @classmethod
    def from_dict(cls, value: object) -> MemoryBlobReference:
        """Parse a compact reference without defaulting or repair."""

        if not isinstance(value, dict) or set(value) != _REFERENCE_FIELDS:
            raise ValueError("Memory blob reference has an invalid schema")
        return cls(
            digest=value.get("digest"),  # type: ignore[arg-type]
            size_bytes=value.get("sizeBytes"),  # type: ignore[arg-type]
        )


def canonical_memory_blob_bytes(value: Any) -> bytes:
    """Encode one bounded strict JSON value into its canonical blob bytes."""

    validate_memory_authority_value(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("Memory blob value is not canonical JSON") from error
    if not encoded or len(encoded) > MAX_MEMORY_BLOB_BYTES:
        raise MemoryAuthorityLimitError("memory_blob_limit_exceeded")
    return encoded


def memory_blob_digest(encoded: bytes) -> str:
    """Return the lowercase SHA-256 address of canonical blob bytes."""

    if not isinstance(encoded, bytes):
        raise TypeError("Memory blob content must be bytes")
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("Memory blob digest is invalid")
    return value


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _same_file_version(first: os.stat_result, second: os.stat_result) -> bool:
    # Publishing uses a hard link followed by removal of its private temporary
    # name.  That removal legitimately changes ctime/link-count while an
    # idempotent concurrent reader may already have the immutable target open.
    # The inode, size, mtime, exact bytes, and content digest still bind the
    # version relevant to this store.
    return (
        _same_inode(first, second)
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


def _require_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise MemoryStoreUnsafePath("Memory blob directory is unsafe")


def _require_blob_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise MemoryStoreUnsafePath("Memory blob path is unsafe")
    if not 0 < metadata.st_size <= MAX_MEMORY_BLOB_BYTES:
        raise MemoryStoreConflict("Memory blob has an invalid size")


def _fsync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise MemoryStoreUnavailable("Memory blob directory is unavailable") from error


def _write_all(descriptor: int, content: bytes) -> None:
    position = 0
    while position < len(content):
        written = os.write(descriptor, content[position:])
        if written <= 0:
            raise OSError("short Memory blob write")
        position += written


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_MEMORY_BLOB_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if not content or len(content) > MAX_MEMORY_BLOB_BYTES:
        raise MemoryStoreConflict("Memory blob has an invalid size")
    return content


def _decode_blob(
    content: bytes,
    *,
    expected_digest: str,
    expected_size: int | None,
) -> Any:
    if expected_size is not None and len(content) != expected_size:
        raise MemoryStoreConflict("Memory blob size does not match its reference")
    if memory_blob_digest(content) != expected_digest:
        raise MemoryStoreConflict("Memory blob digest does not match its address")
    try:
        text = content.decode("utf-8")
        parsed = parse_memory_authority_json(text)
        canonical = canonical_memory_blob_bytes(parsed)
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        raise MemoryStoreConflict("Memory blob is not strict canonical JSON") from error
    if canonical != content:
        raise MemoryStoreConflict("Memory blob is not canonical JSON")
    return parsed


class MemoryBlobStore:
    """Store immutable JSON blobs below one Memory scope authority root."""

    def __init__(
        self,
        scope_root: str | Path,
        *,
        _scope_publication: MemoryScopePublication | None = None,
    ) -> None:
        self._scope_root = Path(scope_root)
        self._blob_root = self._scope_root / MEMORY_BLOB_DIRECTORY_NAME
        self._scope_publication = _scope_publication

    @classmethod
    def from_scope_publication(
        cls,
        publication: MemoryScopePublication,
    ) -> MemoryBlobStore:
        """Create a blob store bound to an open scope publication session."""

        from minicode.memory_authority_publisher import MemoryScopePublication

        if not isinstance(publication, MemoryScopePublication):
            raise TypeError("publication must be a MemoryScopePublication")
        publication.verify()
        return cls(
            publication.path,
            _scope_publication=publication,
        )

    @property
    def root(self) -> Path:
        """Return the fixed auxiliary directory used by this store."""

        return self._blob_root

    def put_json(self, value: Any) -> MemoryBlobReference:
        """Publish a canonical JSON value once and return its content address.

        A concurrent writer of the same bytes is idempotent.  An existing file
        at the address must contain those exact canonical bytes; any collision,
        corruption, redirect, or special file fails closed.
        """

        encoded = canonical_memory_blob_bytes(value)
        reference = MemoryBlobReference(
            digest=memory_blob_digest(encoded),
            size_bytes=len(encoded),
        )
        # Ordinary Memory telemetry rewrites the compact authority frequently.
        # Avoid manufacturing and fsyncing a temporary hard-link source for a
        # blob that was already verified in this content-addressed store.
        try:
            existing = self.read_json(reference)
        except MemoryBlobMissingError:
            pass
        else:
            if canonical_memory_blob_bytes(existing) != encoded:
                raise MemoryStoreConflict("Memory blob address is occupied")
            return reference
        if os.name == "posix":
            self._put_posix(reference, encoded)
        else:
            self._put_portable(reference, encoded)
        return reference

    def read_json(self, reference: MemoryBlobReference | str) -> Any:
        """Read and verify one blob without creating any filesystem state."""

        if isinstance(reference, MemoryBlobReference):
            digest = reference.digest
            expected_size: int | None = reference.size_bytes
        else:
            digest = _require_digest(reference)
            expected_size = None
        filename = f"{digest}.json"
        if os.name == "posix":
            root_fd, blob_fd = self._open_posix_roots(create=False)
            try:
                result = self._read_posix_blob(
                    blob_fd,
                    filename,
                    digest=digest,
                    expected_size=expected_size,
                )
                self._verify_posix_roots(root_fd, blob_fd)
                return result
            finally:
                os.close(blob_fd)
                os.close(root_fd)
        return self._read_portable_blob(
            self._blob_root / filename,
            digest=digest,
            expected_size=expected_size,
        )

    def _open_posix_roots(self, *, create: bool) -> tuple[int, int]:
        root_fd: int | None = None
        blob_fd: int | None = None
        opened = False
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0)
            if self._scope_publication is not None:
                root_fd = self._scope_publication.duplicate_scope_fd()
                root_before = os.fstat(root_fd)
            else:
                if create:
                    MemoryAuthorityPublisher.ensure_directory(self._scope_root)
                try:
                    root_before = os.lstat(self._scope_root)
                except FileNotFoundError as error:
                    raise MemoryBlobMissingError(
                        "Memory blob scope root is missing"
                    ) from error
                except OSError as error:
                    raise MemoryStoreUnavailable(
                        "Memory blob scope root is unavailable"
                    ) from error
                _require_directory(root_before)
                root_fd = os.open(self._scope_root, directory_flags)
                root_opened = os.fstat(root_fd)
                if not _same_inode(root_before, root_opened):
                    raise MemoryStoreUnsafePath(
                        "Memory blob scope root changed"
                    )
            _require_directory(root_before)

            try:
                blob_before = os.stat(
                    MEMORY_BLOB_DIRECTORY_NAME,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
                blob_created = False
            except FileNotFoundError:
                if not create:
                    raise MemoryBlobMissingError(
                        "Memory blob directory is missing"
                    )
                try:
                    os.mkdir(MEMORY_BLOB_DIRECTORY_NAME, 0o700, dir_fd=root_fd)
                    blob_created = True
                except FileExistsError:
                    blob_created = False
                blob_before = os.stat(
                    MEMORY_BLOB_DIRECTORY_NAME,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
            _require_directory(blob_before)
            blob_fd = os.open(
                MEMORY_BLOB_DIRECTORY_NAME,
                directory_flags,
                dir_fd=root_fd,
            )
            blob_opened = os.fstat(blob_fd)
            if not _same_inode(blob_before, blob_opened):
                raise MemoryStoreUnsafePath("Memory blob directory changed")
            if blob_created:
                # The blob file itself and blob directory are fsynced by the
                # publish path below.  Persist this newly created directory's
                # name in the scope root before allowing a standalone
                # ``put_json`` call to report success.
                _fsync_directory(blob_fd)
                _fsync_directory(root_fd)
                if self._scope_publication is not None:
                    self._scope_publication.verify()
            self._verify_posix_roots(
                root_fd,
                blob_fd,
                root_before=root_before,
                blob_before=blob_before,
            )
            opened = True
            return root_fd, blob_fd
        except (MemoryStoreConflict, MemoryStoreUnsafePath):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory blob storage is unavailable"
            ) from error
        finally:
            if not opened and blob_fd is not None:
                os.close(blob_fd)
            if not opened and root_fd is not None:
                os.close(root_fd)

    def _verify_posix_roots(
        self,
        root_fd: int,
        blob_fd: int,
        *,
        root_before: os.stat_result | None = None,
        blob_before: os.stat_result | None = None,
    ) -> None:
        try:
            root_opened = os.fstat(root_fd)
            if self._scope_publication is None:
                root_path = os.lstat(self._scope_root)
            else:
                self._scope_publication.verify()
                root_path = root_opened
            blob_path = os.stat(
                MEMORY_BLOB_DIRECTORY_NAME,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            blob_opened = os.fstat(blob_fd)
        except OSError as error:
            raise MemoryStoreUnsafePath("Memory blob directory changed") from error
        _require_directory(root_path)
        _require_directory(root_opened)
        _require_directory(blob_path)
        _require_directory(blob_opened)
        if (
            not _same_inode(root_path, root_opened)
            or not _same_inode(blob_path, blob_opened)
            or (root_before is not None and not _same_inode(root_before, root_opened))
            or (blob_before is not None and not _same_inode(blob_before, blob_opened))
        ):
            raise MemoryStoreUnsafePath("Memory blob directory changed")
        if self._scope_publication is not None:
            if (
                root_opened.st_dev,
                root_opened.st_ino,
            ) != self._scope_publication.scope_identity:
                raise MemoryStoreUnsafePath("Memory blob scope root changed")

    def _put_posix(
        self,
        reference: MemoryBlobReference,
        encoded: bytes,
    ) -> None:
        root_fd, blob_fd = self._open_posix_roots(create=True)
        temporary_name: str | None = None
        temporary_fd: int | None = None
        published = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            for _attempt in range(16):
                candidate = f".memory-blob-{uuid.uuid4().hex}.tmp"
                try:
                    temporary_fd = os.open(candidate, flags, 0o600, dir_fd=blob_fd)
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_fd is None or temporary_name is None:
                raise MemoryStoreUnavailable(
                    "Memory blob temporary file is unavailable"
                )
            os.fchmod(temporary_fd, 0o600)
            _write_all(temporary_fd, encoded)
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            self._verify_posix_roots(root_fd, blob_fd)
            try:
                os.link(
                    temporary_name,
                    reference.filename,
                    src_dir_fd=blob_fd,
                    dst_dir_fd=blob_fd,
                    follow_symlinks=False,
                )
                published = True
            except FileExistsError:
                existing = self._read_posix_blob(
                    blob_fd,
                    reference.filename,
                    digest=reference.digest,
                    expected_size=reference.size_bytes,
                )
                if canonical_memory_blob_bytes(existing) != encoded:
                    raise MemoryStoreConflict("Memory blob address is occupied")
            _fsync_directory(blob_fd)
            self._verify_posix_roots(root_fd, blob_fd)
        except (
            MemoryAuthorityLimitError,
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            MemoryStoreUnsafePath,
            TypeError,
            ValueError,
        ):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory blob could not be persisted"
            ) from error
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=blob_fd)
                    if published:
                        _fsync_directory(blob_fd)
                except FileNotFoundError:
                    pass
                except (OSError, MemoryStoreUnavailable):
                    # The immutable target was already fsynced.  A leftover
                    # private temporary file is degraded storage health, not a
                    # reason to misreport the committed blob as absent.
                    pass
            os.close(blob_fd)
            os.close(root_fd)

    def _read_posix_blob(
        self,
        blob_fd: int,
        filename: str,
        *,
        digest: str,
        expected_size: int | None,
    ) -> Any:
        descriptor: int | None = None
        try:
            try:
                path_before = os.stat(
                    filename,
                    dir_fd=blob_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError as error:
                raise MemoryBlobMissingError("Memory blob is missing") from error
            _require_blob_file(path_before)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(filename, flags, dir_fd=blob_fd)
            opened_before = os.fstat(descriptor)
            _require_blob_file(opened_before)
            if not _same_inode(path_before, opened_before):
                raise MemoryStoreConflict("Memory blob changed during read")
            content = _read_all(descriptor)
            opened_after = os.fstat(descriptor)
            path_after = os.stat(
                filename,
                dir_fd=blob_fd,
                follow_symlinks=False,
            )
            _require_blob_file(opened_after)
            _require_blob_file(path_after)
            if (
                not _same_file_version(path_before, path_after)
                or not _same_file_version(opened_before, opened_after)
                or not _same_inode(path_after, opened_after)
            ):
                raise MemoryStoreConflict("Memory blob changed during read")
            return _decode_blob(
                content,
                expected_digest=digest,
                expected_size=expected_size,
            )
        except (
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            MemoryStoreUnsafePath,
        ):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable("Memory blob is unavailable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _prepare_portable_roots(self, *, create: bool) -> None:
        if create:
            try:
                self._scope_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            except FileExistsError:
                pass
            except OSError as error:
                raise MemoryStoreUnavailable(
                    "Memory blob scope root is unavailable"
                ) from error
        try:
            root_before = os.lstat(self._scope_root)
        except FileNotFoundError as error:
            raise MemoryBlobMissingError(
                "Memory blob scope root is missing"
            ) from error
        _require_directory(root_before)
        if create:
            try:
                self._blob_root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError as error:
                raise MemoryStoreUnavailable(
                    "Memory blob directory is unavailable"
                ) from error
        try:
            blob_metadata = os.lstat(self._blob_root)
        except FileNotFoundError as error:
            raise MemoryBlobMissingError(
                "Memory blob directory is missing"
            ) from error
        _require_directory(blob_metadata)

    def _put_portable(
        self,
        reference: MemoryBlobReference,
        encoded: bytes,
    ) -> None:
        self._prepare_portable_roots(create=True)
        temporary = self._blob_root / f".memory-blob-{uuid.uuid4().hex}.tmp"
        target = self._blob_root / reference.filename
        descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                existing = self._read_portable_blob(
                    target,
                    digest=reference.digest,
                    expected_size=reference.size_bytes,
                )
                if canonical_memory_blob_bytes(existing) != encoded:
                    raise MemoryStoreConflict("Memory blob address is occupied")
        except (
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            MemoryStoreUnsafePath,
            TypeError,
            ValueError,
        ):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory blob could not be persisted"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _read_portable_blob(
        self,
        path: Path,
        *,
        digest: str,
        expected_size: int | None,
    ) -> Any:
        self._prepare_portable_roots(create=False)
        descriptor: int | None = None
        try:
            try:
                path_before = os.lstat(path)
            except FileNotFoundError as error:
                raise MemoryBlobMissingError("Memory blob is missing") from error
            _require_blob_file(path_before)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path, flags)
            opened_before = os.fstat(descriptor)
            _require_blob_file(opened_before)
            if not _same_inode(path_before, opened_before):
                raise MemoryStoreConflict("Memory blob changed during read")
            content = _read_all(descriptor)
            opened_after = os.fstat(descriptor)
            path_after = os.lstat(path)
            _require_blob_file(opened_after)
            _require_blob_file(path_after)
            if (
                not _same_file_version(path_before, path_after)
                or not _same_file_version(opened_before, opened_after)
                or not _same_inode(path_after, opened_after)
            ):
                raise MemoryStoreConflict("Memory blob changed during read")
            return _decode_blob(
                content,
                expected_digest=digest,
                expected_size=expected_size,
            )
        except (
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            MemoryStoreUnsafePath,
        ):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable("Memory blob is unavailable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)


__all__ = [
    "MAX_MEMORY_BLOB_BYTES",
    "MEMORY_BLOB_DIRECTORY_NAME",
    "MemoryBlobMissingError",
    "MemoryBlobReference",
    "MemoryBlobStore",
    "canonical_memory_blob_bytes",
    "memory_blob_digest",
]
