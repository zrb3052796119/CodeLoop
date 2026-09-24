"""Bounded, race-resistant I/O for persistent Memory authority files.

All Memory readers cross this seam before parsing.  A source is either absent,
or it is a stable regular file no larger than the shared authority budget and
valid UTF-8.  Special files, symlinks, replacements during the read, invalid
UTF-8, and over-budget data are fixed-vocabulary failures rather than empty
stores.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_AUTHORITY_FILE_BYTES = 2 * 1024 * 1024
# ``MAX_AUTHORITY_ENTRIES`` is the bounded legacy *input* budget.  It is kept
# as a compatibility alias because older readers import that name.  Versioned
# authorities have a smaller runtime contract and must never publish more than
# ``MAX_CURRENT_AUTHORITY_ENTRIES``.
MAX_LEGACY_AUTHORITY_ENTRIES = 1_000
MAX_AUTHORITY_ENTRIES = MAX_LEGACY_AUTHORITY_ENTRIES
MAX_CURRENT_AUTHORITY_ENTRIES = 200
MAX_CURRENT_AUTHORITY_CONTENT_BYTES = 25 * 1024
MAX_AUTHORITY_AUDIT_RECORDS = 10_000
CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION = 6
# v2 predates the per-entry authority-state seal. v3 has the seal but embeds
# complete write receipts in memory.json. v4 externalizes those receipts but
# still embeds an ever-growing approval audit. v5 adds the segmented audit but
# predates scope-level replay receipts for observational/maintenance writes.
# Writable readers migrate each predecessor explicitly before publishing v6.
MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS = frozenset({2, 3, 4, 5})

MAX_MEMORY_ENTRY_ID_BYTES = 128
MEMORY_ENTRY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
MEMORY_ENTRY_ID_RE = re.compile(MEMORY_ENTRY_ID_PATTERN)

# JSON numbers cross Python and browser/JavaScript projection boundaries.  A
# counter above the largest exactly representable JSON integer would no longer
# have one canonical value in all supported readers.
MAX_MEMORY_COUNTER = (1 << 53) - 1
MAX_MEMORY_TIMESTAMP = 253_402_300_799.0
MIN_MEMORY_USEFULNESS_SCORE = -1.0
MAX_MEMORY_USEFULNESS_SCORE = 1.0
MAX_MEMORY_JSON_NESTING = 128
MAX_MEMORY_JSON_NODES = 250_000


class MemoryAuthoritySourceError(RuntimeError):
    """A persisted authority source could not be read safely."""


class MemoryAuthorityTooLargeError(MemoryAuthoritySourceError):
    """A regular authority source exceeds the shared byte budget."""


class MemoryAuthorityLimitError(ValueError):
    """A proposed authority commit exceeds a reader-enforced limit."""


def reject_duplicate_json_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Build one JSON object while rejecting ambiguous duplicate members."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("memory_authority_duplicate_json_member")
        result[key] = value
    return result


def reject_nonfinite_json_constant(value: str) -> Any:
    """Reject Python's non-standard NaN/Infinity JSON extensions."""

    raise ValueError(f"memory_authority_nonfinite_json_number:{value}")


def validate_memory_authority_value(value: Any) -> None:
    """Reject JSON values that do not have one bounded cross-reader meaning."""

    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    node_count = 0
    while stack:
        current, depth, exiting = stack.pop()
        if exiting:
            active_containers.discard(id(current))
            continue
        node_count += 1
        if node_count > MAX_MEMORY_JSON_NODES or depth > MAX_MEMORY_JSON_NESTING:
            raise ValueError("memory_authority_json_structure_too_large")
        if isinstance(current, str):
            try:
                current.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError("memory_authority_string_not_utf8") from error
            continue
        if current is None or isinstance(current, bool):
            continue
        if type(current) is int:
            if not -MAX_MEMORY_COUNTER <= current <= MAX_MEMORY_COUNTER:
                raise ValueError("memory_authority_json_integer_out_of_range")
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("memory_authority_nonfinite_json_number")
            continue
        if isinstance(current, dict):
            identity = id(current)
            if identity in active_containers:
                raise ValueError("memory_authority_json_cycle")
            active_containers.add(identity)
            stack.append((current, depth, True))
            for key, nested in current.items():
                if not isinstance(key, str):
                    raise ValueError("memory_authority_json_key_not_string")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise ValueError("memory_authority_string_not_utf8") from error
                stack.append((nested, depth + 1, False))
            continue
        if isinstance(current, list):
            identity = id(current)
            if identity in active_containers:
                raise ValueError("memory_authority_json_cycle")
            active_containers.add(identity)
            stack.append((current, depth, True))
            stack.extend((nested, depth + 1, False) for nested in current)
            continue
        raise ValueError("memory_authority_json_value_invalid")


def parse_memory_authority_json(text: str) -> Any:
    """Parse strict JSON shared by every Memory authority reader.

    Python's default decoder accepts duplicate members and non-standard
    ``NaN``/``Infinity`` values.  Both make an authority ambiguous across
    readers, so the canonical seam rejects them recursively before any entry
    object is constructed.
    """

    if not isinstance(text, str):
        raise TypeError("Memory authority JSON source must be text")
    parsed = json.loads(
        text,
        object_pairs_hook=reject_duplicate_json_object_pairs,
        parse_constant=reject_nonfinite_json_constant,
    )
    validate_memory_authority_value(parsed)
    return parsed


def is_memory_entry_id(value: object) -> bool:
    """Return whether ``value`` is one canonical bounded Memory identity."""

    if not isinstance(value, str) or MEMORY_ENTRY_ID_RE.fullmatch(value) is None:
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_MEMORY_ENTRY_ID_BYTES
    except UnicodeEncodeError:
        return False


@dataclass(frozen=True, slots=True)
class MemoryAuthoritySource:
    """One stable, bounded authority read with strict UTF-8 text."""

    data: bytes
    text: str


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _same_file_version(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        _same_inode(first, second)
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
        and first.st_ctime_ns == second.st_ctime_ns
    )


def _require_regular_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise MemoryAuthoritySourceError("memory_authority_unavailable")
    if metadata.st_size > MAX_AUTHORITY_FILE_BYTES:
        raise MemoryAuthorityTooLargeError("memory_authority_too_large")


def _read_open_file(file_fd: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_AUTHORITY_FILE_BYTES + 1
    while remaining:
        chunk = os.read(file_fd, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > MAX_AUTHORITY_FILE_BYTES:
        raise MemoryAuthorityTooLargeError("memory_authority_too_large")
    return content


def read_memory_authority_file(
    root: str | Path,
    filename: str,
    *,
    platform_name: str | None = None,
) -> MemoryAuthoritySource | None:
    """Read one bounded regular UTF-8 file without following symlinks.

    On POSIX the child is opened relative to a verified directory descriptor.
    Both the root and child path are compared with their open descriptors
    before and after the read so a concurrent replacement fails closed.
    """

    root = Path(root)
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
    ):
        raise ValueError("filename must be one basename")
    platform = os.name if platform_name is None else platform_name
    try:
        root_path_before = os.lstat(root)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MemoryAuthoritySourceError("memory_authority_unavailable") from error
    if not stat.S_ISDIR(root_path_before.st_mode):
        raise MemoryAuthoritySourceError("memory_authority_unavailable")

    root_fd: int | None = None
    file_fd: int | None = None
    try:
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags |= getattr(os, "O_NONBLOCK", 0)
        if platform == "posix":
            root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            root_flags |= getattr(os, "O_CLOEXEC", 0)
            root_flags |= getattr(os, "O_NOFOLLOW", 0)
            root_fd = os.open(root, root_flags)
            root_fd_before = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_fd_before.st_mode)
                or not _same_inode(root_path_before, root_fd_before)
            ):
                raise MemoryAuthoritySourceError("memory_authority_unavailable")
            try:
                path_before = os.stat(
                    filename,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            _require_regular_file(path_before)
            file_fd = os.open(filename, file_flags, dir_fd=root_fd)
            file_fd_before = os.fstat(file_fd)
            if not _same_inode(path_before, file_fd_before):
                raise MemoryAuthoritySourceError("memory_authority_unavailable")
        else:
            if os.path.normcase(os.path.realpath(root)) != os.path.normcase(
                os.path.abspath(root)
            ):
                raise MemoryAuthoritySourceError("memory_authority_unavailable")
            path = root / filename
            try:
                path_before = os.lstat(path)
            except FileNotFoundError:
                return None
            _require_regular_file(path_before)
            file_fd = os.open(path, file_flags)
            file_fd_before = os.fstat(file_fd)
            if not _same_inode(path_before, file_fd_before):
                raise MemoryAuthoritySourceError("memory_authority_unavailable")

        _require_regular_file(file_fd_before)
        content = _read_open_file(file_fd)

        file_fd_after = os.fstat(file_fd)
        root_path_after = os.lstat(root)
        if platform == "posix":
            root_fd_after = os.fstat(root_fd)
            path_after = os.stat(
                filename,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(root_fd_after.st_mode)
                or not _same_inode(root_path_before, root_path_after)
                or not _same_inode(root_fd_before, root_fd_after)
                or not _same_inode(root_path_after, root_fd_after)
            ):
                raise MemoryAuthoritySourceError("memory_authority_unavailable")
        else:
            path_after = os.lstat(root / filename)
            if (
                not stat.S_ISDIR(root_path_after.st_mode)
                or not _same_inode(root_path_before, root_path_after)
            ):
                raise MemoryAuthoritySourceError("memory_authority_unavailable")
        _require_regular_file(path_after)
        _require_regular_file(file_fd_after)
        if (
            not _same_file_version(path_before, path_after)
            or not _same_file_version(file_fd_before, file_fd_after)
            or not _same_inode(path_after, file_fd_after)
        ):
            raise MemoryAuthoritySourceError("memory_authority_unavailable")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MemoryAuthoritySourceError(
                "memory_authority_unavailable"
            ) from error
        return MemoryAuthoritySource(data=content, text=text)
    except MemoryAuthoritySourceError:
        raise
    except OSError as error:
        raise MemoryAuthoritySourceError("memory_authority_unavailable") from error
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass


def validate_memory_authority_commit(
    encoded: bytes,
    *,
    entry_count: int,
    audit_count: int,
) -> None:
    """Reject a commit that its canonical reader would reject next time."""

    if (
        len(encoded) > MAX_AUTHORITY_FILE_BYTES
        or entry_count > MAX_CURRENT_AUTHORITY_ENTRIES
        or audit_count > MAX_AUTHORITY_AUDIT_RECORDS
        or entry_count < 0
        or audit_count < 0
    ):
        raise MemoryAuthorityLimitError("memory_authority_limit_exceeded")
    try:
        encoded.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MemoryAuthorityLimitError(
            "memory_authority_limit_exceeded"
        ) from error


__all__ = [
    "CURRENT_MEMORY_AUTHORITY_SCHEMA_VERSION",
    "MAX_AUTHORITY_AUDIT_RECORDS",
    "MAX_AUTHORITY_ENTRIES",
    "MAX_AUTHORITY_FILE_BYTES",
    "MAX_CURRENT_AUTHORITY_ENTRIES",
    "MAX_CURRENT_AUTHORITY_CONTENT_BYTES",
    "MAX_LEGACY_AUTHORITY_ENTRIES",
    "MAX_MEMORY_COUNTER",
    "MAX_MEMORY_ENTRY_ID_BYTES",
    "MAX_MEMORY_JSON_NESTING",
    "MAX_MEMORY_JSON_NODES",
    "MAX_MEMORY_TIMESTAMP",
    "MAX_MEMORY_USEFULNESS_SCORE",
    "MIGRATABLE_MEMORY_AUTHORITY_SCHEMA_VERSIONS",
    "MEMORY_ENTRY_ID_PATTERN",
    "MEMORY_ENTRY_ID_RE",
    "MIN_MEMORY_USEFULNESS_SCORE",
    "MemoryAuthorityLimitError",
    "MemoryAuthoritySource",
    "MemoryAuthoritySourceError",
    "MemoryAuthorityTooLargeError",
    "is_memory_entry_id",
    "parse_memory_authority_json",
    "read_memory_authority_file",
    "reject_duplicate_json_object_pairs",
    "reject_nonfinite_json_constant",
    "validate_memory_authority_commit",
    "validate_memory_authority_value",
]
