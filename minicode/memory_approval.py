"""Persistent, workspace-scoped authority for reviewing automatic Memory.

This module projects the existing Memory store; it does not create a second
database.  Reviews are bounded and a decision is fenced to the exact projected
content/state by ``memoryreviewrev_*``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import minicode.memory as memory_module
from minicode.memory import (
    MemoryEntry,
    MemoryManager,
    MemoryScope,
    MemoryTier,
    _APPROVAL_APPROVED,
    _APPROVAL_PENDING,
    _APPROVAL_REJECTED,
    _SAFETY_UNSAFE,
    _approval_hash_for_entry,
    assess_memory_safety,
)
from minicode.memory_store import MemoryStoreBusy, MemoryStoreConflict


MemoryDecision = Literal["approve", "reject"]

MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
MEMORY_REVIEW_REVISION_RE = re.compile(r"^memoryreviewrev_[0-9a-f]{64}$")
MEMORY_APPROVAL_REVISION_RE = re.compile(r"^memoryapprovalrev_[0-9a-f]{64}$")

MAX_MEMORY_ID_BYTES = 160
MAX_REVIEW_PREVIEW_BYTES = 8 * 1024
MAX_REVIEW_ITEM_BYTES = 12 * 1024
MAX_PENDING_ITEMS = 20
MAX_SNAPSHOT_BYTES = 128 * 1024
REVIEW_PROJECTION_VERSION = 1
_MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
_MAX_SOURCE_ENTRIES = 1_000

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,255}\b")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"
)
_SECRET_RE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|auth[_ -]?token|token|password|"
    r"secret|credential|authorization|cookie|private[_ -]?key)\b\s*[:=]\s*\S+"
)
_WEB_URL_RE = re.compile(r"(?i)https?://[^\s'\"<>]+")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9._~%+\-:])/(?!/)[^\s'\"<>]+")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9._~%+\-])(?:[a-z]:[\\/]|\\\\)[^\s]+")
_HOME_PATH_RE = re.compile(r"(?<![A-Za-z0-9._~%+\-])~(?:/|\\)[^\s]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_REDACTED_PREVIEW = "[REDACTED SENSITIVE MEMORY]"
_UNSAFE_PREVIEW = "[UNSAFE MEMORY CONTENT HIDDEN]"
_OVERSIZE_PREVIEW = "[MEMORY REVIEW TOO LARGE]"
_PUBLIC_CATEGORIES = frozenset(
    {
        "general",
        "note",
        "directive",
        "architecture",
        "code-pattern",
        "testing",
        "configuration",
        "workflow",
        "security",
        "performance",
        "convention",
        "decision",
        "preference",
        "pattern",
        "insight",
    }
)
_PUBLIC_SOURCES = frozenset({"reflection", "curator", "user", "manual", "unknown"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _platform_name() -> str:
    return os.name


def _iso_time(value: datetime | float) -> str:
    if isinstance(value, bool):
        raise TypeError("bool is not a timestamp")
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(float(value), tz=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _canonical_hash(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()


def _truncate_utf8(value: str, budget: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= budget:
        return value, False
    return encoded[:budget].decode("utf-8", errors="ignore"), True


def _contains_secret_or_path(value: str) -> bool:
    without_urls = _WEB_URL_RE.sub("[WEB_URL]", value)
    return bool(
        _BEARER_RE.search(value)
        or _KEY_RE.search(value)
        or _AWS_KEY_RE.search(value)
        or _GITHUB_TOKEN_RE.search(value)
        or _JWT_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
        or _CREDENTIAL_URL_RE.search(value)
        or _SECRET_RE.search(value)
        or _POSIX_PATH_RE.search(without_urls)
        or _WINDOWS_PATH_RE.search(without_urls)
        or _HOME_PATH_RE.search(without_urls)
    )


class MemoryApprovalError(RuntimeError):
    """Fixed-code failure safe to map to an HTTP response."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MemoryApprovalDecision:
    memory_id: str
    status: Literal["approved", "rejected"]
    decision: MemoryDecision
    decision_accepted: bool
    updated_at: str


class MemoryApprovalAuthority:
    """Deep persistent approval boundary used by TUI-compatible core and HTTP."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
        store_timeout: float = 5.0,
    ) -> None:
        self._workspace = Path(workspace).resolve(strict=False)
        self._clock = clock
        self._store_timeout = store_timeout

    def _manager(self) -> MemoryManager:
        self._validate_candidate_paths()
        manager = MemoryManager(
            project_root=self._workspace,
            store_timeout=self._store_timeout,
        )
        self._validate_scope_roots(manager)
        return manager

    def _scope_roots(self) -> tuple[tuple[MemoryScope, Path], ...]:
        return (
            (MemoryScope.USER, Path(memory_module.MINI_CODE_DIR) / "memory"),
            (MemoryScope.PROJECT, self._workspace / ".mini-code-memory"),
            (MemoryScope.LOCAL, self._workspace / ".mini-code-memory-local"),
        )

    @staticmethod
    def _read_regular_file(root: Path, filename: str) -> bytes | None:
        """Read one bounded regular file without creating or following anything."""
        try:
            root_stat = root.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise MemoryApprovalError("memory_approval_unavailable") from error
        if not stat.S_ISDIR(root_stat.st_mode):
            raise MemoryApprovalError("memory_approval_unavailable")

        root_fd: int | None = None
        file_fd: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            if _platform_name() == "posix":
                root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                root_flags |= getattr(os, "O_CLOEXEC", 0)
                root_flags |= getattr(os, "O_NOFOLLOW", 0)
                root_fd = os.open(root, root_flags)
                root_fd_stat = os.fstat(root_fd)
                if (
                    not stat.S_ISDIR(root_fd_stat.st_mode)
                    or root_fd_stat.st_dev != root_stat.st_dev
                    or root_fd_stat.st_ino != root_stat.st_ino
                ):
                    raise MemoryApprovalError("memory_approval_unavailable")
                try:
                    file_fd = os.open(filename, flags, dir_fd=root_fd)
                except FileNotFoundError:
                    return None
            else:
                if os.path.normcase(os.path.realpath(root)) != os.path.normcase(
                    os.path.abspath(root)
                ):
                    raise MemoryApprovalError("memory_approval_unavailable")
                path = root / filename
                try:
                    path_stat = os.lstat(path)
                except FileNotFoundError:
                    return None
                if (
                    not stat.S_ISREG(path_stat.st_mode)
                    or path_stat.st_size > _MAX_SOURCE_FILE_BYTES
                ):
                    raise MemoryApprovalError("memory_approval_unavailable")
                try:
                    file_fd = os.open(path, flags)
                except FileNotFoundError:
                    return None
                current_root_stat = os.lstat(root)
                current_path_stat = os.lstat(path)
                opened_path_stat = os.fstat(file_fd)
                if (
                    not stat.S_ISDIR(current_root_stat.st_mode)
                    or current_root_stat.st_dev != root_stat.st_dev
                    or current_root_stat.st_ino != root_stat.st_ino
                    or not stat.S_ISREG(current_path_stat.st_mode)
                    or current_path_stat.st_dev != path_stat.st_dev
                    or current_path_stat.st_ino != path_stat.st_ino
                    or opened_path_stat.st_dev != path_stat.st_dev
                    or opened_path_stat.st_ino != path_stat.st_ino
                ):
                    raise MemoryApprovalError("memory_approval_unavailable")
            file_stat = os.fstat(file_fd)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size > _MAX_SOURCE_FILE_BYTES
            ):
                raise MemoryApprovalError("memory_approval_unavailable")
            chunks: list[bytes] = []
            remaining = _MAX_SOURCE_FILE_BYTES + 1
            while remaining:
                chunk = os.read(file_fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > _MAX_SOURCE_FILE_BYTES:
                raise MemoryApprovalError("memory_approval_unavailable")
            return content
        except MemoryApprovalError:
            raise
        except OSError as error:
            raise MemoryApprovalError("memory_approval_unavailable") from error
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if root_fd is not None:
                os.close(root_fd)

    @staticmethod
    def _decode_source(content: bytes) -> str:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MemoryApprovalError("memory_approval_failed") from error

    @staticmethod
    def _interpret_loaded_entry(
        entry: MemoryEntry,
        raw_data: dict[str, object],
    ) -> MemoryEntry:
        """Apply the write loader's compatibility rules in memory only."""
        safety = assess_memory_safety(entry.content, source=entry.source or "load")
        raw_has_approval = "approval_status" in raw_data
        raw_has_hash = "approval_content_hash" in raw_data
        previous_hash = entry.approval_content_hash
        current_hash = _approval_hash_for_entry(entry)

        entry.safety_status = safety.status
        entry.safety_reason = safety.reason
        if raw_has_hash and previous_hash and previous_hash != current_hash:
            raise MemoryApprovalError("memory_approval_failed")
        if not raw_has_approval:
            if safety.status == "safe":
                entry.approval_status = (
                    _APPROVAL_APPROVED
                    if entry.lifecycle_status == "active"
                    else _APPROVAL_PENDING
                )
            elif safety.status == "unsafe":
                entry.approval_status = _APPROVAL_REJECTED
            else:
                entry.approval_status = _APPROVAL_PENDING
            entry.approval_reason = safety.reason or "legacy migration"
            entry.approval_actor = "migration"
        elif safety.status == "unsafe":
            entry.approval_status = _APPROVAL_REJECTED
            entry.approval_reason = safety.reason
            entry.approval_actor = "safety_gate"
        elif not raw_has_hash and (
            safety.status == "suspicious"
            and entry.approval_status != _APPROVAL_REJECTED
        ):
            entry.approval_status = _APPROVAL_PENDING
            entry.approval_reason = safety.reason
            entry.approval_actor = "migration"

        if entry.approval_status == _APPROVAL_REJECTED:
            entry.lifecycle_status = "rejected"
            entry.curator_locked = False
        if entry.safety_status == "unsafe":
            entry.lifecycle_status = "rejected"
            entry.tier_reason = "safety_gate"
        entry.approval_content_hash = current_hash
        if not entry.approval_actor:
            entry.approval_actor = "migration"
        return entry

    def _read_json_entries(
        self,
        scope: MemoryScope,
        content: bytes,
    ) -> list[MemoryEntry]:
        try:
            parsed = json.loads(self._decode_source(content))
        except json.JSONDecodeError as error:
            raise MemoryApprovalError("memory_approval_failed") from error
        is_valid, _ = memory_module._validate_memory_data(parsed)
        if not is_valid or len(parsed["entries"]) > _MAX_SOURCE_ENTRIES:
            raise MemoryApprovalError("memory_approval_failed")

        entries: list[MemoryEntry] = []
        for raw_entry in parsed["entries"]:
            if not isinstance(raw_entry, dict):
                raise MemoryApprovalError("memory_approval_failed")
            if raw_entry.get("scope", scope.value) != scope.value:
                raise MemoryApprovalError("memory_approval_failed")
            normalized = dict(raw_entry)
            normalized["scope"] = scope.value
            normalized.setdefault("created_at", 0.0)
            normalized.setdefault("updated_at", normalized["created_at"])
            normalized.setdefault("last_accessed", 0.0)
            try:
                entry = MemoryEntry.from_dict(normalized)
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise MemoryApprovalError("memory_approval_failed") from error
            if not self._valid_memory_id(entry.id) or not entry.content:
                raise MemoryApprovalError("memory_approval_failed")
            entries.append(self._interpret_loaded_entry(entry, raw_entry))
        return entries

    def _read_markdown_entries(
        self,
        scope: MemoryScope,
        content: bytes,
    ) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        category = "general"
        for raw_line in self._decode_source(content).splitlines():
            line = raw_line.strip()
            if line.startswith("#") or line.startswith("*") or not line:
                if line.startswith("## "):
                    category = line[3:].strip().lower()
                continue
            if not line.startswith("- "):
                continue
            if len(entries) >= _MAX_SOURCE_ENTRIES:
                raise MemoryApprovalError("memory_approval_failed")
            entry_content = line[2:]
            tags: list[str] = []
            if "`" in entry_content:
                for tag_match in re.findall(r"`([^`]+)`", entry_content):
                    tags.extend(tag_match.split())
                entry_content = re.sub(r"`[^`]+`", "", entry_content).strip()
            entry = MemoryEntry(
                id=f"{scope.value}-{len(entries) + 1}",
                scope=scope,
                category=category,
                content=entry_content,
                created_at=0.0,
                updated_at=0.0,
                last_accessed=0.0,
                tags=tags,
            )
            if not entry.content:
                raise MemoryApprovalError("memory_approval_failed")
            entries.append(self._interpret_loaded_entry(entry, {}))
        return entries

    def _read_scope_entries(self, scope: MemoryScope, root: Path) -> list[MemoryEntry]:
        audit = self._read_regular_file(root, "approval_audit.json")
        if audit is not None:
            try:
                audit_data = json.loads(self._decode_source(audit))
            except json.JSONDecodeError as error:
                raise MemoryApprovalError("memory_approval_failed") from error
            if (
                not isinstance(audit_data, dict)
                or not isinstance(audit_data.get("records", []), list)
                or not all(
                    isinstance(record, dict)
                    for record in audit_data.get("records", [])
                )
            ):
                raise MemoryApprovalError("memory_approval_failed")

        memory_json = self._read_regular_file(root, "memory.json")
        if memory_json is not None:
            return self._read_json_entries(scope, memory_json)
        memory_md = self._read_regular_file(root, "MEMORY.md")
        if memory_md is not None:
            return self._read_markdown_entries(scope, memory_md)
        return []

    def _read_pending_entries(self) -> list[MemoryEntry]:
        """Load pending entries through a bounded, strictly no-write seam."""
        self._validate_candidate_paths()
        entries: list[MemoryEntry] = []
        seen_ids: set[str] = set()
        for scope, root in self._scope_roots():
            for entry in self._read_scope_entries(scope, root):
                if entry.id in seen_ids:
                    raise MemoryApprovalError("memory_approval_failed")
                seen_ids.add(entry.id)
                if entry.approval_status == _APPROVAL_PENDING:
                    entries.append(entry)
        return sorted(entries, key=lambda entry: (entry.created_at, entry.scope.value, entry.id))

    def _validate_candidate_paths(self) -> None:
        data_root = Path(memory_module.MINI_CODE_DIR)
        roots = (
            data_root / "memory",
            self._workspace / ".mini-code-memory",
            self._workspace / ".mini-code-memory-local",
        )
        try:
            if data_root.is_symlink():
                raise MemoryApprovalError("memory_approval_unavailable")
            for root in roots:
                if root.is_symlink():
                    raise MemoryApprovalError("memory_approval_unavailable")
                for filename in ("memory.json", "MEMORY.md", "approval_audit.json"):
                    if (root / filename).is_symlink():
                        raise MemoryApprovalError("memory_approval_unavailable")
        except OSError as error:
            raise MemoryApprovalError("memory_approval_unavailable") from error

    def _validate_scope_roots(self, manager: MemoryManager) -> None:
        workspace = self._workspace
        for scope in MemoryScope:
            root = manager._get_scope_path(scope)  # one audited internal store seam
            if scope in {MemoryScope.PROJECT, MemoryScope.LOCAL}:
                try:
                    if root.parent.resolve(strict=False) != workspace:
                        raise MemoryApprovalError("memory_approval_unavailable")
                except OSError as error:
                    raise MemoryApprovalError("memory_approval_unavailable") from error
            try:
                if root.is_symlink():
                    raise MemoryApprovalError("memory_approval_unavailable")
                for filename in ("memory.json", "MEMORY.md", "approval_audit.json"):
                    if (root / filename).is_symlink():
                        raise MemoryApprovalError("memory_approval_unavailable")
            except OSError as error:
                raise MemoryApprovalError("memory_approval_unavailable") from error

    @staticmethod
    def _valid_memory_id(memory_id: object) -> bool:
        return (
            isinstance(memory_id, str)
            and len(memory_id.encode("utf-8")) <= MAX_MEMORY_ID_BYTES
            and MEMORY_ID_RE.fullmatch(memory_id) is not None
        )

    @staticmethod
    def _review_revision(entry: MemoryEntry) -> str:
        content_hash = hashlib.sha256(entry.content.encode("utf-8")).hexdigest()
        return _canonical_hash(
            "memoryreviewrev_",
            {
                "projectionVersion": REVIEW_PROJECTION_VERSION,
                "memoryId": entry.id,
                "scope": entry.scope.value,
                "approvalStatus": entry.approval_status,
                "lifecycleStatus": entry.lifecycle_status,
                "safetyStatus": entry.safety_status,
                "approvalContentHash": entry.approval_content_hash,
                "contentHash": content_hash,
            },
        )

    @staticmethod
    def _safe_preview(entry: MemoryEntry) -> tuple[dict[str, object], bool]:
        original = entry.content
        persistence_sanitized = (
            entry.metadata.get("persistence_sanitized", {})
            if isinstance(entry.metadata, dict)
            else {}
        )
        if (
            isinstance(persistence_sanitized, dict)
            and persistence_sanitized.get("content") is True
        ):
            return {
                "contentPreview": _REDACTED_PREVIEW,
                "complete": False,
                "truncated": False,
                "redacted": True,
            }, False
        content = _ANSI_RE.sub("", original)
        content = _CONTROL_RE.sub("", content)
        if content != original:
            return {
                "contentPreview": _REDACTED_PREVIEW,
                "complete": False,
                "truncated": False,
                "redacted": True,
            }, False
        safety = assess_memory_safety(content, source="memory_review")
        if safety.status == _SAFETY_UNSAFE:
            return {
                "contentPreview": _UNSAFE_PREVIEW,
                "complete": False,
                "truncated": False,
                "redacted": True,
            }, False
        if _contains_secret_or_path(content):
            return {
                "contentPreview": _REDACTED_PREVIEW,
                "complete": False,
                "truncated": False,
                "redacted": True,
            }, False
        preview, truncated = _truncate_utf8(content, MAX_REVIEW_PREVIEW_BYTES)
        review = {
            "contentPreview": preview,
            "complete": not truncated,
            "truncated": truncated,
            "redacted": False,
        }
        reviewable = (
            not truncated
            and entry.lifecycle_status == "active"
            and not entry.curator_locked
            and entry.tier != MemoryTier.ARCHIVAL
            and entry.approval_content_hash == _approval_hash_for_entry(entry)
            and safety.status in {"safe", "suspicious"}
        )
        return review, reviewable

    def _public_item(self, entry: MemoryEntry) -> dict[str, object]:
        review, reviewable = self._safe_preview(entry)
        risk = {"safe": "low", "suspicious": "medium", "unsafe": "high"}.get(
            entry.safety_status, "high"
        )
        item: dict[str, object] = {
            "memoryId": entry.id,
            "scope": entry.scope.value,
            "scopeKind": "user/global" if entry.scope == MemoryScope.USER else "workspace",
            "category": entry.category if entry.category in _PUBLIC_CATEGORIES else "other",
            "tier": entry.tier.value,
            "source": entry.source if entry.source in _PUBLIC_SOURCES else "unknown",
            "createdAt": _iso_time(entry.created_at),
            "risk": risk,
            "safetyStatus": entry.safety_status if entry.safety_status in {"safe", "suspicious", "unsafe"} else "unsafe",
            "reviewable": reviewable,
            "review": review,
            "reviewRevision": self._review_revision(entry),
            "choices": ["approve", "reject"] if reviewable else ["reject"],
        }
        if len(json.dumps(item, ensure_ascii=False).encode("utf-8")) > MAX_REVIEW_ITEM_BYTES:
            item["reviewable"] = False
            item["review"] = {
                "contentPreview": _OVERSIZE_PREVIEW,
                "complete": False,
                "truncated": True,
                "redacted": False,
            }
            item["choices"] = ["reject"]
        # Final recursive/serialized defense: any projected secret/path turns the
        # content review into a fixed deny-only placeholder.
        if _contains_secret_or_path(json.dumps(item, ensure_ascii=False)):
            item["reviewable"] = False
            item["review"] = {
                "contentPreview": _REDACTED_PREVIEW,
                "complete": False,
                "truncated": False,
                "redacted": True,
            }
            item["choices"] = ["reject"]
        return item

    def snapshot(self) -> dict[str, object]:
        """Return the current bounded, safe pending-review projection."""
        try:
            pending = self._read_pending_entries()
            items: list[dict[str, object]] = []
            diagnostics: list[dict[str, str]] = []
            for entry in pending:
                if len(items) >= MAX_PENDING_ITEMS:
                    diagnostics.append({"code": "items_limited"})
                    break
                item = self._public_item(entry)
                candidate = items + [item]
                if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES - 2048:
                    diagnostics.append({"code": "snapshot_limited"})
                    break
                items.append(item)
            semantic_revision = _canonical_hash(
                "memoryapprovalrev_",
                [
                    {
                        "memoryId": entry.id,
                        "reviewRevision": self._review_revision(entry),
                    }
                    for entry in pending
                ],
            )
            latest = max(
                (entry.updated_at for entry in pending),
                default=self._clock().timestamp(),
            )
            snapshot: dict[str, object] = {
                "schemaVersion": 1,
                "generatedAt": _iso_time(self._clock()),
                "mode": "read-only",
                "source": {
                    "status": "live",
                    "updatedAt": _iso_time(latest),
                    "message": None,
                },
                "revision": semantic_revision,
                "items": items,
                "diagnostics": diagnostics,
            }
            if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
                raise MemoryApprovalError("memory_approval_failed")
            return snapshot
        except MemoryApprovalError:
            raise
        except (MemoryStoreBusy, MemoryStoreConflict) as error:
            raise MemoryApprovalError(error.code) from error
        except BaseException as error:  # noqa: BLE001 - safe fixed authority error
            raise MemoryApprovalError("memory_approval_failed") from error

    def revision(self) -> str:
        return str(self.snapshot()["revision"])

    def decide(
        self,
        *,
        memory_id: str,
        decision: MemoryDecision,
        review_revision: str,
    ) -> MemoryApprovalDecision:
        """Commit a decision iff the review still describes current authority."""
        if not self._valid_memory_id(memory_id):
            raise MemoryApprovalError("invalid_memory_id")
        if not isinstance(decision, str) or decision not in {"approve", "reject"}:
            raise MemoryApprovalError("invalid_decision")
        if (
            not isinstance(review_revision, str)
            or MEMORY_REVIEW_REVISION_RE.fullmatch(review_revision) is None
        ):
            raise MemoryApprovalError("invalid_review_revision")
        try:
            manager = self._manager()

            def commit() -> MemoryApprovalDecision:
                self._validate_scope_roots(manager)
                scope, entry = manager._find_entry_by_id(memory_id)
                if scope is None or entry is None or entry.scope != scope:
                    raise MemoryApprovalError("memory_approval_not_found")
                if entry.approval_status != _APPROVAL_PENDING:
                    same = (
                        entry.approval_actor == "dashboard_user"
                        and (
                            (
                                decision == "approve"
                                and entry.approval_status == _APPROVAL_APPROVED
                                and entry.approval_reason == "dashboard_approved"
                            )
                            or (
                                decision == "reject"
                                and entry.approval_status == _APPROVAL_REJECTED
                                and entry.approval_reason == "dashboard_rejected"
                            )
                        )
                    )
                    if same:
                        return MemoryApprovalDecision(
                            memory_id=entry.id,
                            status=entry.approval_status,
                            decision=decision,
                            decision_accepted=False,
                            updated_at=_iso_time(entry.approval_decided_at or entry.updated_at),
                        )
                    raise MemoryApprovalError("memory_already_decided")

                item = self._public_item(entry)
                current_revision = str(item["reviewRevision"])
                if not hmac.compare_digest(current_revision, review_revision):
                    raise MemoryApprovalError("memory_review_stale")
                if decision == "approve" and (
                    item["reviewable"] is not True
                    or item["choices"] != ["approve", "reject"]
                ):
                    raise MemoryApprovalError("memory_not_reviewable")
                mutation = manager.decide_pending_entry(
                    entry.id,
                    decision,
                    actor="dashboard_user",
                    reason=(
                        "dashboard_approved"
                        if decision == "approve"
                        else "dashboard_rejected"
                    ),
                )
                expected = _APPROVAL_APPROVED if decision == "approve" else _APPROVAL_REJECTED
                if mutation.status != expected or not mutation.decision_accepted:
                    raise MemoryApprovalError("memory_not_reviewable")
                _, committed = manager._find_entry_by_id(entry.id)
                if committed is None:
                    raise MemoryApprovalError("memory_write_conflict")
                return MemoryApprovalDecision(
                    memory_id=mutation.memory_id,
                    status=mutation.status,
                    decision=decision,
                    decision_accepted=True,
                    updated_at=_iso_time(committed.approval_decided_at),
                )

            return manager.coordinated_write(tuple(MemoryScope), commit)
        except MemoryApprovalError:
            raise
        except MemoryStoreBusy as error:
            raise MemoryApprovalError("memory_store_busy") from error
        except MemoryStoreConflict as error:
            raise MemoryApprovalError("memory_write_conflict") from error
        except BaseException as error:  # noqa: BLE001 - safe fixed authority error
            raise MemoryApprovalError("memory_approval_failed") from error


__all__ = [
    "MAX_PENDING_ITEMS",
    "MAX_REVIEW_ITEM_BYTES",
    "MAX_REVIEW_PREVIEW_BYTES",
    "MAX_SNAPSHOT_BYTES",
    "MEMORY_APPROVAL_REVISION_RE",
    "MEMORY_ID_RE",
    "MEMORY_REVIEW_REVISION_RE",
    "MemoryApprovalAuthority",
    "MemoryApprovalDecision",
    "MemoryApprovalError",
]
