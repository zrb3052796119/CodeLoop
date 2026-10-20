"""Protect a bounded, host-observed implementation candidate from baseline reverts.

This observer never restores or writes files. A destructive in-place baseline
check is rejected before a normal file tool writes it; opaque commands and
external mutations are checked again before delivery. Historical verification
is bound to the exact observed scope, file identities, and mutation epoch. It
is deliberately not a claim that the task or the entire workspace is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from typing import Any, Sequence
import uuid

from minicode.tooling import ToolResult
from minicode.verification_observation import normalize_tool_verification


_WRITE_TOOLS = frozenset({"write_file", "edit_file", "executor_test"})
_CHECK_TOOLS = frozenset({"test_runner", "run_command"})
_SOURCE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".kt", ".swift",
    ".rb", ".php", ".cs", ".vue", ".svelte", ".sh", ".sql",
})


class CandidateProtectionError(RuntimeError):
    """A candidate cannot safely be observed within its configured limits."""


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    allowed: bool
    code: str
    message: str
    candidate_retained: bool = False
    verified_current: bool = False


@dataclass(frozen=True, slots=True)
class _FileImage:
    data: bytes | None
    identity: tuple[int, ...] | None


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    """Opaque, one-use verification lease; only the host should hold this."""

    owner: str
    serial: int
    mutation_epoch: int
    scope_epoch: int
    calls_digest: str
    images: tuple[tuple[str, _FileImage], ...]
    usable: bool


@dataclass(frozen=True, slots=True)
class _VerifiedCandidate:
    images: tuple[tuple[str, _FileImage], ...]
    mutation_epoch: int
    scope_epoch: int
    check_kind: str


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    """True when both stats name one file.

    Windows ``lstat`` often reports ``st_ino`` 0 while ``fstat`` reports the
    file index. A zero inode is unknown, not a different file.
    """
    if left.st_dev != right.st_dev:
        return False
    if left.st_ino == 0 or right.st_ino == 0:
        return True
    return left.st_ino == right.st_ino


def _reparse_point(value: os.stat_result) -> bool:
    if stat.S_ISLNK(value.st_mode):
        return True
    attributes = getattr(value, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _calls_digest(calls: Sequence[dict[str, Any]]) -> str:
    data = json.dumps(calls, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def _is_source(path: str) -> bool:
    target = Path(path)
    return (
        target.suffix.casefold() in _SOURCE_SUFFIXES
        and not any(part.casefold() in {"test", "tests", "fixtures"} for part in target.parts)
        and not target.name.casefold().startswith("test_")
        and not target.stem.casefold().endswith("_test")
    )


class ImplementationCandidateGuard:
    """Bounded candidate retention and non-mutating write/delivery admission.

    ``allowed_paths`` is an exact file scope, including not-yet-created files.
    With no explicit scope, an existing permission manager is mandatory and
    file targets are registered immediately before their first actual write.
    That dynamic mode observes only those files, not an entire worktree.

    Host integration must call ``preflight_write`` immediately before each
    atomic file write, and must call ``capture_before`` / ``observe_results``
    around each individual executing check. A mixed write/check batch earns no
    verification lease. Call ``delivery_check`` on *every* exit path, including
    cancellation and deadline exits, and never turn a rejection into success.

    Each snapshot has fixed file/byte ceilings. The guard retains the baseline
    and one candidate, and only its newest observation lease can be consumed.
    No original files are temporarily changed, so exceptions and hard
    termination cannot strand a baseline used for a counterexample. Such
    checks need a separate sandbox.
    """

    def __init__(
        self,
        cwd: str,
        *,
        allowed_paths: Sequence[str] | None = None,
        permissions: object | None = None,
        source_paths: Sequence[str] | None = None,
        max_files: int = 64,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_snapshot_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if min(max_files, max_file_bytes, max_snapshot_bytes) <= 0:
            raise ValueError("candidate limits must be positive")
        if allowed_paths is None and not callable(getattr(permissions, "ensure_path_access", None)):
            raise ValueError("dynamic candidate scope requires existing path permissions")
        self._root = Path(cwd).resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("candidate workspace must be a directory")
        root_stat = self._root.stat()
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        self._permissions = permissions
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_snapshot_bytes = max_snapshot_bytes
        self._lock = threading.RLock()
        self._owner = uuid.uuid4().hex
        self._serial = 0
        self._active_observation: int | None = None
        self._scope_epoch = 0
        self._mutation_epoch = 0
        self._baseline: dict[str, _FileImage] = {}
        self._candidate: _VerifiedCandidate | None = None
        self._uncertain = False
        self._failed_current_check = False
        self._allowed = None if allowed_paths is None else frozenset(self._relative(path) for path in allowed_paths)
        self._sources = None if source_paths is None else frozenset(self._relative(path) for path in source_paths)
        if self._allowed is not None:
            for path in sorted(self._allowed):
                self._register(path)

    def _relative(self, path: str) -> str:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise CandidateProtectionError("candidate path is invalid")
        target = Path(path)
        if target.is_absolute():
            try:
                target = target.relative_to(self._root)
            except ValueError as exc:
                raise CandidateProtectionError("candidate path escapes workspace") from exc
        if not target.parts or any(part in {"..", ""} for part in target.parts):
            raise CandidateProtectionError("candidate path escapes workspace")
        return target.as_posix()

    def _authorize(self, relative: str) -> None:
        if self._allowed is not None and relative not in self._allowed:
            raise CandidateProtectionError("candidate path exceeds its exact file scope")
        ensure = getattr(self._permissions, "ensure_path_access", None)
        if callable(ensure):
            target = str(self._root / relative)
            # Observation cannot use write scope as an implied read grant.
            ensure(target, "write")
            ensure(target, "read")

    def _read_image_by_path(self, relative: str) -> _FileImage:
        """Observe one file on Windows, where a directory cannot be opened.

        Symlinks and other reparse points are rejected with ``lstat`` before
        the file is read. The POSIX ``dir_fd`` path is unchanged.
        """
        parts = Path(relative).parts
        current = self._root
        file_fd: int | None = None
        try:
            root_stat = current.lstat()
            if stat.S_ISLNK(root_stat.st_mode):
                raise CandidateProtectionError("candidate workspace identity changed")
            if root_stat.st_dev != self._root_identity[0] or (
                root_stat.st_ino != 0
                and self._root_identity[1] != 0
                and root_stat.st_ino != self._root_identity[1]
            ):
                raise CandidateProtectionError("candidate workspace identity changed")
            for part in parts[:-1]:
                current = current / part
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    return _FileImage(None, None)
                if _reparse_point(info) or not stat.S_ISDIR(info.st_mode):
                    raise CandidateProtectionError("candidate target is not safely observable")
                if not current.resolve(strict=False).is_relative_to(self._root):
                    raise CandidateProtectionError("candidate target is not safely observable")
            target = current / parts[-1]
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                return _FileImage(None, None)
            if _reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
                raise CandidateProtectionError("candidate target is not a bounded regular file")
            if metadata.st_size > self._max_file_bytes:
                raise CandidateProtectionError("candidate target is not a bounded regular file")
            if not target.resolve(strict=False).is_relative_to(self._root):
                raise CandidateProtectionError("candidate target is not safely observable")
            file_fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > self._max_file_bytes:
                raise CandidateProtectionError("candidate target is not a bounded regular file")
            data = bytearray()
            while len(data) <= self._max_file_bytes:
                chunk = os.read(file_fd, min(65536, self._max_file_bytes + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(file_fd)
            if len(data) > self._max_file_bytes or _file_identity(before) != _file_identity(after):
                raise CandidateProtectionError("candidate target changed during observation")
            confirmed = target.lstat()
            if (
                _reparse_point(confirmed)
                or not stat.S_ISREG(confirmed.st_mode)
                or not _same_inode(confirmed, before)
            ):
                raise CandidateProtectionError("candidate lexical target changed during observation")
            return _FileImage(bytes(data), _file_identity(after))
        except FileNotFoundError:
            return _FileImage(None, None)
        except CandidateProtectionError:
            raise
        except (OSError, ValueError) as exc:
            raise CandidateProtectionError("candidate target is not safely observable") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)

    def _read_image(self, relative: str) -> _FileImage:
        self._authorize(relative)
        if os.name == "nt":
            return self._read_image_by_path(relative)
        if (
            not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd
        ):
            raise CandidateProtectionError("safe descriptor-relative candidate reads unavailable")
        descriptors: list[int] = []
        file_fd: int | None = None
        parts = Path(relative).parts
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            root_fd = os.open(self._root, flags)
            descriptors.append(root_fd)
            root_stat = os.fstat(root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
                raise CandidateProtectionError("candidate workspace identity changed")
            parent = root_fd
            for part in parts[:-1]:
                try:
                    parent = os.open(part, flags, dir_fd=parent)
                except FileNotFoundError:
                    return _FileImage(None, None)
                descriptors.append(parent)
            try:
                file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                return _FileImage(None, None)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > self._max_file_bytes:
                raise CandidateProtectionError("candidate target is not a bounded regular file")
            data = bytearray()
            while len(data) <= self._max_file_bytes:
                part = os.read(file_fd, min(65536, self._max_file_bytes + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
            after = os.fstat(file_fd)
            if len(data) > self._max_file_bytes or _file_identity(before) != _file_identity(after):
                raise CandidateProtectionError("candidate target changed during observation")
            # Verify the lexical path still names the opened inode. A replaced
            # parent directory must not make an unlinked old file authoritative.
            confirmed_parent = os.open(self._root, flags)
            descriptors.append(confirmed_parent)
            confirmed_root = os.fstat(confirmed_parent)
            if (confirmed_root.st_dev, confirmed_root.st_ino) != self._root_identity:
                raise CandidateProtectionError("candidate workspace identity changed")
            for part in parts[:-1]:
                confirmed_parent = os.open(part, flags, dir_fd=confirmed_parent)
                descriptors.append(confirmed_parent)
            confirmed = os.stat(parts[-1], dir_fd=confirmed_parent, follow_symlinks=False)
            if not stat.S_ISREG(confirmed.st_mode) or _file_identity(confirmed) != _file_identity(after):
                raise CandidateProtectionError("candidate lexical target changed during observation")
            return _FileImage(bytes(data), _file_identity(after))
        except (OSError, ValueError) as exc:
            raise CandidateProtectionError("candidate target is not safely observable") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _register(self, relative: str) -> None:
        self._authorize(relative)
        if relative in self._baseline:
            return
        if len(self._baseline) >= self._max_files:
            raise CandidateProtectionError("candidate file limit reached")
        image = self._read_image(relative)
        retained_size = sum(len(item.data or b"") for item in self._baseline.values())
        if retained_size + len(image.data or b"") > self._max_snapshot_bytes:
            raise CandidateProtectionError("candidate baseline byte limit reached")
        self._baseline[relative] = image
        self._scope_epoch += 1

    def _snapshot(self) -> tuple[tuple[str, _FileImage], ...]:
        result = []
        size = 0
        for path in sorted(self._baseline):
            image = self._read_image(path)
            size += len(image.data or b"")
            if size > self._max_snapshot_bytes:
                raise CandidateProtectionError("candidate snapshot byte limit reached")
            result.append((path, image))
        return tuple(result)

    def _source(self, path: str) -> bool:
        return path in self._sources if self._sources is not None else _is_source(path)

    def _changed_source(self, images: tuple[tuple[str, _FileImage], ...]) -> bool:
        return any(self._source(path) and image.data != self._baseline[path].data for path, image in images)

    def _decision(self, allowed: bool, code: str, message: str, *, verified: bool = False) -> CandidateDecision:
        return CandidateDecision(allowed, code, message, self._candidate is not None, verified)

    def preflight_write(self, path: str, next_content: str | bytes) -> CandidateDecision:
        """Reject a verified source's destructive baseline rewrite before I/O.

        This does not authorize the actual write or replace its normal conflict
        and cancellation checks. It never writes saved bytes over a user change.
        """
        with self._lock:
            try:
                relative = self._relative(path)
                self._register(relative)
                if not isinstance(next_content, (str, bytes)):
                    raise CandidateProtectionError("candidate proposed bytes are invalid")
                proposed = next_content.encode("utf-8") if isinstance(next_content, str) else next_content
                if len(proposed) > self._max_file_bytes:
                    raise CandidateProtectionError("candidate proposed file byte limit reached")
                current = self._read_image(relative)
                candidate = dict(self._candidate.images).get(relative) if self._candidate else None
                baseline = self._baseline[relative]
                if (
                    self._source(relative)
                    and candidate is not None
                    and candidate.data != baseline.data
                    and proposed == baseline.data
                    and current.data != proposed
                ):
                    return self._decision(False, "verified_candidate_baseline_revert", (
                        "In-place baseline rollback would remove an observed verified source candidate. "
                        "Run baseline/counterexample checks in a separate isolated workspace; keep "
                        "the implementation bytes in this workspace, or make an explicit new repair."
                    ))
                return self._decision(True, "candidate_write_admitted", "Candidate protection permits this proposed write; normal write permissions and conflict checks still apply.")
            except Exception:
                self._uncertain = True
                return self._decision(False, "candidate_observation_unavailable", "Candidate protection cannot safely bind this file; the write is denied.")

    def capture_before(self, calls: Sequence[dict[str, Any]], *, mutation_epoch: int = 0) -> CandidateObservation:
        """Capture a one-use lease immediately before an individual check."""
        with self._lock:
            self._serial += 1
            self._active_observation = self._serial
            self._mutation_epoch = mutation_epoch
            usable = False
            images: tuple[tuple[str, _FileImage], ...] = ()
            try:
                for call in calls:
                    if call.get("toolName") in _WRITE_TOOLS:
                        inputs = call.get("input")
                        path = inputs.get("path") if isinstance(inputs, dict) else None
                        self._register(self._relative(path))
                images = self._snapshot()
                usable = (
                    len(calls) == 1
                    and calls[0].get("toolName") in _CHECK_TOOLS
                    and bool(images)
                )
            except Exception:
                self._uncertain = True
            return CandidateObservation(
                self._owner, self._serial, mutation_epoch, self._scope_epoch,
                _calls_digest(calls), images, usable,
            )

    def observe_results(
        self,
        calls_results: Sequence[tuple[dict[str, Any], ToolResult]],
        before: CandidateObservation,
        *,
        mutation_epoch: int = 0,
    ) -> CandidateDecision:
        """Retain bytes only for a real typed passed check on an unchanged lease."""
        with self._lock:
            owner_matches = isinstance(before, CandidateObservation) and before.owner == self._owner
            active = owner_matches and before.serial == self._active_observation
            if active:
                self._active_observation = None
            self._mutation_epoch = mutation_epoch
            try:
                current = self._snapshot()
                if self._candidate and not self._changed_source(current):
                    return self._decision(False, "verified_candidate_lost", "All observed source changes have returned to the turn baseline. Saved candidate bytes were retained but were not automatically written over the current workspace.")
                if not (
                    active and before.usable and before.scope_epoch == self._scope_epoch
                    and before.mutation_epoch == mutation_epoch
                    and before.calls_digest == _calls_digest([call for call, _result in calls_results])
                    and before.images == current and len(calls_results) == 1
                ):
                    return self._decision(True, "candidate_check_unbound", "No new verification evidence was bound to this workspace state.")
                call, result = calls_results[0]
                verification = normalize_tool_verification(call.get("toolName"), result.verification)
                if verification is None or verification["kind"] == "review":
                    return self._decision(True, "candidate_check_untyped", "This result does not carry an independent implementation check marker.")
                if not result.ok or verification["outcome"] != "passed":
                    self._failed_current_check = True
                    return self._decision(True, "candidate_check_failed", "The observed check failed; no candidate is promoted and historical success does not verify the current state.")
                if not self._changed_source(current):
                    return self._decision(True, "candidate_source_unchanged", "A check passed but no implementation source bytes differ from the observed baseline.")
                self._candidate = _VerifiedCandidate(current, mutation_epoch, self._scope_epoch, str(verification["kind"]))
                self._failed_current_check = False
                self._uncertain = False
                return self._decision(True, "verified_candidate_retained", "The exact observed scope passed a typed check and its bounded bytes were retained. Overall task completion remains unconfirmed.", verified=True)
            except Exception:
                self._uncertain = True
                return self._decision(False, "candidate_observation_unavailable", "Current candidate state cannot be safely observed; delivery must remain incomplete.")

    def delivery_check(self) -> CandidateDecision:
        """Detect a lost candidate without overwriting concurrent user changes."""
        with self._lock:
            try:
                current = self._snapshot()
                if self._uncertain:
                    return self._decision(False, "candidate_observation_unavailable", "Candidate observation failed earlier; re-observe and verify the authorized current state before delivery.")
                if self._candidate is None:
                    return self._decision(True, "candidate_not_verified", "No verified implementation candidate was retained; task completion requires separate host validation.")
                if not self._changed_source(current):
                    return self._decision(False, "verified_candidate_lost", "All observed source changes are at the turn baseline. The retained candidate is historical evidence only; it was not restored over concurrent changes and cannot count as delivered implementation.")
                verified = (
                    self._candidate.images == current
                    and self._candidate.scope_epoch == self._scope_epoch
                    and self._candidate.mutation_epoch == self._mutation_epoch
                    and not self._failed_current_check
                )
                return self._decision(True, "candidate_current" if verified else "candidate_requires_reverification", (
                    "The retained candidate bytes and epoch still match the observed scope; overall completion requires separate host validation."
                    if verified else "Current implementation differs from the retained verification state or a later check failed. Historical verification cannot prove current completion."
                ), verified=verified)
            except Exception:
                self._uncertain = True
                return self._decision(False, "candidate_observation_unavailable", "Candidate state cannot be safely observed at delivery; preserve the workspace and report incomplete work.")


__all__ = [
    "CandidateDecision", "CandidateObservation", "CandidateProtectionError",
    "ImplementationCandidateGuard",
]
