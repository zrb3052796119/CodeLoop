"""Minimal ``.env`` file loading (no third-party dependency).

The repository ships a ``.env.example`` describing its environment knobs.
This module provides bounded parsing for callers that explicitly choose a
configuration file and trust boundary; it never discovers or applies a
workspace file implicitly.

Semantics:
- ``KEY=VALUE`` lines; ``#`` comments and blank lines ignored;
- surrounding quotes on the value are stripped; a leading ``export `` is
  tolerated;
- ``read_env_files`` merges in order (later files win) without touching the
  process environment;
- ``apply_env_file`` imports values into ``os.environ`` only for keys not
  already set, so real environment variables always take precedence.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import tempfile
from collections.abc import Collection, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 256 * 1024
_MAX_LINES = 2_000
_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class _PrivateEnvError(RuntimeError):
    """Credential-safe failure carrying only a stable reason code."""


def _private_error(reason: str) -> _PrivateEnvError:
    return _PrivateEnvError(reason)


def _validated_allowed_keys(
    allowed_keys: Collection[str] | None,
) -> frozenset[str] | None:
    if allowed_keys is None:
        return None
    if isinstance(allowed_keys, (str, bytes)):
        raise _private_error("private_env_allowed_key_invalid")
    try:
        candidates = tuple(allowed_keys)
    except TypeError:
        raise _private_error("private_env_allowed_key_invalid") from None
    if any(
        not isinstance(key, str) or _ENV_KEY_PATTERN.fullmatch(key) is None
        for key in candidates
    ):
        raise _private_error("private_env_allowed_key_invalid")
    return frozenset(candidates)


def _check_private_directory(directory: Path) -> None:
    try:
        metadata = os.lstat(directory)
    except OSError:
        raise _private_error("private_env_directory_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise _private_error("private_env_directory_symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _private_error("private_env_parent_not_directory")
    if os.name == "posix":
        if metadata.st_uid != os.geteuid():
            raise _private_error("private_env_directory_owner_unsafe")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise _private_error("private_env_directory_permissions_unsafe")


def _check_private_file_metadata(metadata: os.stat_result) -> None:
    if stat.S_ISLNK(metadata.st_mode):
        raise _private_error("private_env_symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise _private_error("private_env_not_regular")
    if metadata.st_size > _MAX_FILE_BYTES:
        raise _private_error("private_env_too_large")
    if os.name == "posix":
        if metadata.st_uid != os.geteuid():
            raise _private_error("private_env_owner_unsafe")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise _private_error("private_env_permissions_unsafe")


def _read_private_bytes(path: Path) -> bytes | None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise _private_error("private_env_unreadable") from None

    _check_private_directory(path.parent)
    _check_private_file_metadata(metadata)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise _private_error("private_env_unreadable") from None
    try:
        opened_metadata = os.fstat(descriptor)
        _check_private_file_metadata(opened_metadata)
        if (
            opened_metadata.st_dev != metadata.st_dev
            or opened_metadata.st_ino != metadata.st_ino
        ):
            raise _private_error("private_env_changed_during_open")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(_MAX_FILE_BYTES + 1)
    except OSError:
        raise _private_error("private_env_unreadable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > _MAX_FILE_BYTES:
        raise _private_error("private_env_too_large")
    return payload


def _decode_private_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value.startswith('"') or value.endswith('"'):
        if not (value.startswith('"') and value.endswith('"')):
            raise _private_error("private_env_value_quote_invalid")
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            raise _private_error("private_env_value_quote_invalid") from None
        if not isinstance(decoded, str):
            raise _private_error("private_env_value_invalid")
        value = decoded
    elif value.startswith("'") or value.endswith("'"):
        if not (value.startswith("'") and value.endswith("'")):
            raise _private_error("private_env_value_quote_invalid")
        value = value[1:-1]
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise _private_error("private_env_value_invalid")
    return value


def _parse_private_env(payload: bytes) -> dict[str, str]:
    if b"\x00" in payload:
        raise _private_error("private_env_value_invalid")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _private_error("private_env_utf8_invalid") from None
    lines = text.splitlines()
    if len(lines) > _MAX_LINES:
        raise _private_error("private_env_too_many_lines")

    values: dict[str, str] = {}
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            raise _private_error("private_env_line_invalid")
        key, _, raw_value = stripped.partition("=")
        key = key.strip()
        if _ENV_KEY_PATTERN.fullmatch(key) is None:
            raise _private_error("private_env_key_invalid")
        if key in values:
            raise _private_error("private_env_duplicate_key")
        values[key] = _decode_private_value(raw_value)
    return values


def read_private_env_file(
    path: str | Path,
    *,
    allowed_keys: Collection[str] | None = None,
) -> dict[str, str]:
    """Read an owner-private env file without mutating ``os.environ``.

    Missing files return an empty mapping. Existing files fail closed when
    their type, ownership, permissions, encoding, grammar, or limits are
    unsafe. When ``allowed_keys`` is supplied, valid unknown entries remain
    on disk but are not returned to the caller.
    """
    allowed = _validated_allowed_keys(allowed_keys)
    payload = _read_private_bytes(Path(path))
    if payload is None:
        return {}
    values = _parse_private_env(payload)
    if allowed is None:
        return values
    return {key: value for key, value in values.items() if key in allowed}


def _ensure_private_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, mode=_PRIVATE_DIRECTORY_MODE, exist_ok=True)
    except OSError:
        raise _private_error("private_env_directory_unavailable") from None
    try:
        metadata = os.lstat(directory)
    except OSError:
        raise _private_error("private_env_directory_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise _private_error("private_env_directory_symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _private_error("private_env_parent_not_directory")
    if os.name == "posix":
        if metadata.st_uid != os.geteuid():
            raise _private_error("private_env_directory_owner_unsafe")
        try:
            os.chmod(directory, _PRIVATE_DIRECTORY_MODE)
        except OSError:
            raise _private_error("private_env_directory_permissions_unsafe") from None
    _check_private_directory(directory)


def _serialize_private_env(values: Mapping[str, str]) -> bytes:
    lines = [
        f"{key}={json.dumps(value, ensure_ascii=True)}\n"
        for key, value in sorted(values.items())
    ]
    payload = "".join(lines).encode("utf-8")
    if len(lines) > _MAX_LINES:
        raise _private_error("private_env_too_many_lines")
    if len(payload) > _MAX_FILE_BYTES:
        raise _private_error("private_env_too_large")
    return payload


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise _private_error("private_env_directory_sync_failed") from None


@contextmanager
def _private_env_write_lock(destination: Path) -> Iterator[None]:
    lock_path = destination.parent / f".{destination.name}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, _PRIVATE_FILE_MODE)
    except OSError:
        raise _private_error("private_env_lock_unavailable") from None
    try:
        if os.name == "posix":
            import fcntl

            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        yield
    finally:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def _validate_private_updates(
    updates: Mapping[str, str | None],
    allowed_keys: Collection[str] | None,
) -> tuple[frozenset[str] | None, dict[str, str | None]]:
    allowed = _validated_allowed_keys(allowed_keys)
    if not isinstance(updates, Mapping):
        raise _private_error("private_env_updates_invalid")
    validated: dict[str, str | None] = {}
    for key, value in updates.items():
        if not isinstance(key, str) or _ENV_KEY_PATTERN.fullmatch(key) is None:
            raise _private_error("private_env_key_invalid")
        if allowed is not None and key not in allowed:
            raise _private_error("private_env_key_not_allowed")
        if value is not None and not isinstance(value, str):
            raise _private_error("private_env_value_invalid")
        if isinstance(value, str) and any(
            character in value for character in ("\x00", "\r", "\n")
        ):
            raise _private_error("private_env_value_invalid")
        validated[key] = value
    return allowed, validated


def _update_private_env_file_unlocked(
    path: str | Path,
    updates: Mapping[str, str | None],
    *,
    allowed_keys: Collection[str] | None = None,
) -> dict[str, str]:
    """Atomically apply private env updates and return the verified values.

    A string sets a key and ``None`` removes it. Unknown existing keys are
    preserved. If ``allowed_keys`` is provided, update keys outside that set
    fail closed. The file is replaced in-place with mode 0600 after its data
    and containing directory have been synchronised.
    """
    destination = Path(path)
    allowed, validated_updates = _validate_private_updates(updates, allowed_keys)

    _ensure_private_directory(destination.parent)

    existing_payload = _read_private_bytes(destination)
    values = _parse_private_env(existing_payload) if existing_payload is not None else {}
    for key, value in validated_updates.items():
        if value is None:
            values.pop(key, None)
            continue
        values[key] = value

    payload = _serialize_private_env(values)
    temporary: str | None = None
    descriptor = -1
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        if os.name == "posix":
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        if os.name == "posix":
            os.chmod(destination, _PRIVATE_FILE_MODE)
        _fsync_directory(destination.parent)
    except _PrivateEnvError:
        raise
    except OSError:
        raise _private_error("private_env_write_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    verified = read_private_env_file(destination, allowed_keys=allowed)
    expected = (
        values
        if allowed is None
        else {key: value for key, value in values.items() if key in allowed}
    )
    if verified != expected:
        raise _private_error("private_env_verification_failed")
    return verified


def update_private_env_file(
    path: str | Path,
    updates: Mapping[str, str | None],
    *,
    allowed_keys: Collection[str] | None = None,
) -> dict[str, str]:
    """Atomically update a private env file under a cross-process lock."""
    destination = Path(path)
    # Validate before creating the private directory or lock file so malformed
    # input has no filesystem side effect.
    _validate_private_updates(updates, allowed_keys)
    _ensure_private_directory(destination.parent)
    with _private_env_write_lock(destination):
        return _update_private_env_file_unlocked(
            destination,
            updates,
            allowed_keys=allowed_keys,
        )


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse one ``.env`` file into a dict; unreadable files yield ``{}``."""
    try:
        file_path = Path(path)
        if not file_path.is_file() or file_path.stat().st_size > _MAX_FILE_BYTES:
            return {}
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines()[:_MAX_LINES]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or any(character.isspace() for character in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def read_env_files(paths: list[str | Path]) -> dict[str, str]:
    """Merge several ``.env`` files in order; later files override earlier."""
    merged: dict[str, str] = {}
    for path in paths:
        merged.update(parse_env_file(path))
    return merged


def apply_env_file(paths: list[str | Path], *, override: bool = False) -> dict[str, str]:
    """Import ``.env`` values into the process environment.

    Existing variables are preserved unless ``override`` is set, so a real
    ``export`` always beats the file.
    """
    applied: dict[str, str] = {}
    for key, value in read_env_files(paths).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    if applied:
        logger.debug("Loaded %d setting(s) from env file(s)", len(applied))
    return applied


__all__ = [
    "apply_env_file",
    "parse_env_file",
    "read_env_files",
    "read_private_env_file",
    "update_private_env_file",
]
