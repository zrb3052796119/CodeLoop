"""Content-free coordination fences and bounded deletion receipts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - local Dashboard supports POSIX
    fcntl = None  # type: ignore[assignment]


_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_REVISION_RE = re.compile(r"delrev_[0-9a-f]{64}")
_KINDS = frozenset({"conversation", "project-memory"})
_MAX_RECORD_BYTES = 2_048
_MAX_RECEIPTS = 256
_MAX_SCAN = 512
_DEFAULT_TIMEOUT = 5.0
_DEFAULT_RECEIPT_TTL = timedelta(minutes=10)


class DeletionStoreError(RuntimeError):
    """Base fixed-vocabulary deletion coordination failure."""


class DeletionStoreBusy(DeletionStoreError):
    """The coordination lock could not be acquired in time."""


class DeletionStoreUnavailable(DeletionStoreError):
    """Deletion metadata could not be accessed safely."""


class DeletionFenceActive(DeletionStoreError):
    """A writer targeted an identity with an in-progress deletion."""


@dataclass(frozen=True, slots=True)
class DeletionRecord:
    kind: str
    target_id: str
    deletion_revision: str
    status: str
    created_at: str
    expires_at: str | None = None


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _process_lock(path: Path) -> threading.RLock:
    key = os.path.abspath(os.fspath(path))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _workspace_id(workspace: str | Path) -> str:
    resolved = Path(workspace).expanduser().resolve()
    return "ws_" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]


def _iso_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DeletionStoreUnavailable("deletion clock unavailable")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or len(value) != 24 or not value.endswith("Z"):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("invalid timestamp")
    return parsed.astimezone(timezone.utc)


class DeletionLedger:
    """Own one Workspace's safe deletion coordination and short receipts."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        timeout: float = _DEFAULT_TIMEOUT,
        receipt_ttl: timedelta = _DEFAULT_RECEIPT_TTL,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < float(timeout) <= 60
            or not isinstance(receipt_ttl, timedelta)
            or not 1 <= receipt_ttl.total_seconds() <= 86_400
        ):
            raise ValueError("invalid deletion ledger limits")
        self.workspace = Path(workspace).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self.workspace_id = _workspace_id(self.workspace)
        self.root = (
            self.data_dir
            / "dashboard"
            / "workspaces"
            / self.workspace_id
            / "deletions"
        )
        self._lock_path = self.root / "coordination.lock"
        self._clock = clock
        self._timeout = float(timeout)
        self._receipt_ttl = receipt_ttl
        self._process_lock = _process_lock(self._lock_path)

    @staticmethod
    def _validate(kind: object, target_id: object) -> tuple[str, str]:
        if kind not in _KINDS:
            raise ValueError("invalid deletion kind")
        if not isinstance(target_id, str) or _TARGET_RE.fullmatch(target_id) is None:
            raise ValueError("invalid deletion target")
        return str(kind), target_id

    def _record_path(self, kind: str, target_id: str, suffix: str) -> Path:
        safe_kind, safe_target = self._validate(kind, target_id)
        identity = hashlib.sha256(safe_target.encode("ascii")).hexdigest()
        return self.root / f"{safe_kind}-{identity}.{suffix}.json"

    def _ensure_root(self) -> None:
        current = self.data_dir
        try:
            current.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in ("dashboard", "workspaces", self.workspace_id, "deletions"):
                target = current / name
                if target.is_symlink():
                    raise OSError("unsafe deletion root")
                target.mkdir(exist_ok=True, mode=0o700)
                info = os.lstat(target)
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError("unsafe deletion root")
                resolved = target.resolve(strict=True)
                if not resolved.is_relative_to(self.data_dir):
                    raise OSError("unsafe deletion root")
                current = resolved
        except OSError as error:
            raise DeletionStoreUnavailable("deletion store unavailable") from error

    def _open_lock(self) -> int:
        if fcntl is None:
            raise DeletionStoreUnavailable("deletion store unavailable")
        self._ensure_root()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
            info = os.fstat(descriptor)
            path_info = os.lstat(self._lock_path)
            if (
                not stat.S_ISREG(info.st_mode)
                or not stat.S_ISREG(path_info.st_mode)
                or info.st_dev != path_info.st_dev
                or info.st_ino != path_info.st_ino
            ):
                raise OSError("unsafe deletion lock")
            os.fchmod(descriptor, 0o600)
            return descriptor
        except OSError as error:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise DeletionStoreUnavailable("deletion store unavailable") from error

    @contextmanager
    def coordination(self) -> Iterator[None]:
        deadline = time.monotonic() + self._timeout
        if not self._process_lock.acquire(timeout=self._timeout):
            raise DeletionStoreBusy("deletion store busy")
        descriptor: int | None = None
        try:
            descriptor = self._open_lock()
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise DeletionStoreUnavailable(
                            "deletion store unavailable"
                        ) from error
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeletionStoreBusy("deletion store busy") from error
                    time.sleep(min(0.01, remaining))
            yield
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)
            self._process_lock.release()

    def _read(self, path: Path) -> DeletionRecord | None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise DeletionStoreUnavailable("deletion store unavailable") from error
        try:
            info = os.fstat(descriptor)
            path_info = os.lstat(path)
            if (
                not stat.S_ISREG(info.st_mode)
                or not stat.S_ISREG(path_info.st_mode)
                or info.st_dev != path_info.st_dev
                or info.st_ino != path_info.st_ino
                or info.st_size > _MAX_RECORD_BYTES
            ):
                raise DeletionStoreUnavailable("deletion store unavailable")
            raw = os.read(descriptor, _MAX_RECORD_BYTES + 1)
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict) or set(parsed) not in (
                {"schemaVersion", "kind", "targetId", "deletionRevision", "status", "createdAt"},
                {"schemaVersion", "kind", "targetId", "deletionRevision", "status", "createdAt", "expiresAt"},
            ):
                raise ValueError("invalid deletion record")
            record = DeletionRecord(
                kind=parsed["kind"],
                target_id=parsed["targetId"],
                deletion_revision=parsed["deletionRevision"],
                status=parsed["status"],
                created_at=parsed["createdAt"],
                expires_at=parsed.get("expiresAt"),
            )
            if (
                parsed["schemaVersion"] != 1
                or record.kind not in _KINDS
                or _TARGET_RE.fullmatch(record.target_id) is None
                or _REVISION_RE.fullmatch(record.deletion_revision) is None
                or record.status not in {"in_progress", "completed"}
                or (record.status == "in_progress") != (record.expires_at is None)
            ):
                raise ValueError("invalid deletion record")
            _parse_time(record.created_at)
            if record.expires_at is not None:
                _parse_time(record.expires_at)
            return record
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DeletionStoreUnavailable("deletion store unavailable") from error
        finally:
            os.close(descriptor)

    def read_fence(self, kind: str, target_id: str) -> DeletionRecord | None:
        return self._read(self._record_path(kind, target_id, "fence"))

    def read_receipt(self, kind: str, target_id: str) -> DeletionRecord | None:
        record = self._read(self._record_path(kind, target_id, "receipt"))
        if record is None or record.expires_at is None:
            return record
        return record if _parse_time(record.expires_at) > self._clock() else None

    @staticmethod
    def _encoded(record: DeletionRecord) -> bytes:
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "kind": record.kind,
            "targetId": record.target_id,
            "deletionRevision": record.deletion_revision,
            "status": record.status,
            "createdAt": record.created_at,
        }
        if record.expires_at is not None:
            payload["expiresAt"] = record.expires_at
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > _MAX_RECORD_BYTES:
            raise DeletionStoreUnavailable("deletion record too large")
        return encoded

    def _atomic_write(self, path: Path, record: DeletionRecord, *, exclusive: bool) -> None:
        self._ensure_root()
        encoded = self._encoded(record)
        if exclusive:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                if os.write(descriptor, encoded) != len(encoded):
                    raise OSError("short deletion record write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=".deletion-", suffix=".tmp", dir=self.root
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(descriptor)
            os.replace(temporary, path)
            temporary = ""
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def start(self, kind: str, target_id: str, deletion_revision: str) -> DeletionRecord:
        if _REVISION_RE.fullmatch(deletion_revision) is None:
            raise ValueError("invalid deletion revision")
        existing = self.read_fence(kind, target_id)
        if existing is not None:
            return existing
        record = DeletionRecord(
            kind=kind,
            target_id=target_id,
            deletion_revision=deletion_revision,
            status="in_progress",
            created_at=_iso_time(self._clock()),
        )
        try:
            self._atomic_write(
                self._record_path(kind, target_id, "fence"), record, exclusive=True
            )
        except FileExistsError:
            existing = self.read_fence(kind, target_id)
            if existing is None:
                raise DeletionStoreUnavailable("deletion fence unavailable")
            return existing
        return record

    def abandon(self, kind: str, target_id: str) -> None:
        path = self._record_path(kind, target_id, "fence")
        try:
            if path.is_symlink():
                raise OSError("unsafe deletion fence")
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as error:
            raise DeletionStoreUnavailable("deletion fence unavailable") from error

    def complete(self, kind: str, target_id: str, deletion_revision: str) -> DeletionRecord:
        now = self._clock().astimezone(timezone.utc)
        record = DeletionRecord(
            kind=kind,
            target_id=target_id,
            deletion_revision=deletion_revision,
            status="completed",
            created_at=_iso_time(now),
            expires_at=_iso_time(now + self._receipt_ttl),
        )
        self._atomic_write(
            self._record_path(kind, target_id, "receipt"), record, exclusive=False
        )
        self.abandon(kind, target_id)
        self._cleanup_receipts(now)
        return record

    def _cleanup_receipts(self, now: datetime) -> None:
        try:
            candidates = []
            with os.scandir(self.root) as scanner:
                for index, entry in enumerate(scanner):
                    if index >= _MAX_SCAN:
                        break
                    if entry.name.endswith(".receipt.json") and not entry.is_symlink():
                        candidates.append(Path(entry.path))
            live: list[tuple[datetime, Path]] = []
            for path in candidates:
                try:
                    record = self._read(path)
                    if record is None or record.expires_at is None:
                        continue
                    expiry = _parse_time(record.expires_at)
                    if expiry <= now:
                        path.unlink()
                    else:
                        live.append((expiry, path))
                except (DeletionStoreError, OSError, ValueError):
                    continue
            for _, path in sorted(live)[: max(0, len(live) - _MAX_RECEIPTS)]:
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            return

    @contextmanager
    def writer_guard(self, kind: str, target_id: str) -> Iterator[None]:
        with self.coordination():
            if self.read_fence(kind, target_id) is not None:
                raise DeletionFenceActive("deletion in progress")
            yield


@contextmanager
def conversation_write_guard(
    workspace: str | Path,
    session_id: str,
    *,
    data_dir: str | Path,
) -> Iterator[None]:
    ledger = DeletionLedger(workspace, data_dir=data_dir)
    with ledger.writer_guard("conversation", session_id):
        yield


__all__ = [
    "DeletionFenceActive",
    "DeletionLedger",
    "DeletionRecord",
    "DeletionStoreBusy",
    "DeletionStoreError",
    "DeletionStoreUnavailable",
    "conversation_write_guard",
]
