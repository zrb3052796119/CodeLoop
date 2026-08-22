"""Workspace-local authority for deleting one Project Memory identity."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import minicode.memory as memory_module
from minicode.deletion_store import (
    DeletionLedger,
    DeletionRecord,
    DeletionStoreBusy,
    DeletionStoreUnavailable,
)
from minicode.memory import MemoryEntry, MemoryManager, MemoryScope, MemoryTier
from minicode.memory_approval import (
    MEMORY_ID_RE,
    _PUBLIC_CATEGORIES,
    MemoryApprovalAuthority,
    MemoryApprovalError,
)
from minicode.memory_store import (
    MemoryStoreBusy,
    MemoryStoreConflict,
    MemoryStoreUnavailable,
)


_APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected"})
_LIFECYCLE_STATUSES = frozenset(
    {
        "active",
        "pending",
        "rejected",
        "held",
        "archived",
        "deprecated",
        "invalid",
        "archived_duplicate",
    }
)


class ProjectMemoryDeletionError(RuntimeError):
    """One fixed-code Project Memory deletion failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProjectMemoryDeletionError("deletion_unavailable")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ProjectMemoryDeletionAuthority:
    """Preview/delete authority for exactly one current-Workspace Project item."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
        store_timeout: float = 5.0,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        configured_data_dir = (
            Path(memory_module.MINI_CODE_DIR)
            if data_dir is None
            else Path(data_dir)
        )
        self.data_dir = configured_data_dir.expanduser().resolve(strict=False)
        self._clock = clock
        self._store_timeout = store_timeout
        self._reader = MemoryApprovalAuthority(
            self.workspace,
            clock=clock,
            store_timeout=store_timeout,
        )
        self._ledger = DeletionLedger(
            self.workspace,
            data_dir=self.data_dir,
            clock=clock,
            timeout=store_timeout,
        )

    @staticmethod
    def _validate_id(memory_id: object) -> str:
        if not isinstance(memory_id, str) or MEMORY_ID_RE.fullmatch(memory_id) is None:
            raise ProjectMemoryDeletionError("invalid_id")
        return memory_id

    @staticmethod
    def _validate_revision(value: object) -> str:
        from minicode.conversation_deletion import DELETION_REVISION_RE

        if not isinstance(value, str) or DELETION_REVISION_RE.fullmatch(value) is None:
            raise ProjectMemoryDeletionError("invalid_revision")
        return value

    def _read_entries_and_audit(
        self,
        memory_id: str,
    ) -> tuple[list[MemoryEntry], list[dict[str, object]], dict[str, object] | None]:
        self._reader._validate_candidate_paths()
        root = self.workspace / ".mini-code-memory"
        entries = self._reader._read_scope_entries(MemoryScope.PROJECT, root)
        if len(entries) > 1_000 or len({entry.id for entry in entries}) != len(entries):
            raise MemoryApprovalError("memory_approval_failed")

        audit_raw = self._reader._read_regular_file(root, "approval_audit.json")
        audit_records: list[dict[str, object]] = []
        if audit_raw is not None:
            decoded = json.loads(self._reader._decode_source(audit_raw))
            records = decoded.get("records", []) if isinstance(decoded, dict) else None
            if (
                not isinstance(records, list)
                or len(records) > 1_000
                or not all(isinstance(record, dict) for record in records)
            ):
                raise MemoryApprovalError("memory_approval_failed")
            audit_records = [dict(record) for record in records]

        raw_target: dict[str, object] | None = None
        memory_raw = self._reader._read_regular_file(root, "memory.json")
        if memory_raw is not None:
            decoded = json.loads(self._reader._decode_source(memory_raw))
            raw_entries = decoded.get("entries", []) if isinstance(decoded, dict) else None
            if not isinstance(raw_entries, list) or len(raw_entries) > 1_000:
                raise MemoryApprovalError("memory_approval_failed")
            raw_by_id = [
                item
                for item in raw_entries
                if isinstance(item, dict) and item.get("id") == memory_id
            ]
            if len(raw_by_id) > 1:
                raise MemoryApprovalError("memory_approval_failed")
            raw_target = dict(raw_by_id[0]) if raw_by_id else None
        return entries, audit_records, raw_target

    def _plan(
        self,
        memory_id: str,
        *,
        fence: DeletionRecord | None = None,
    ) -> tuple[dict[str, object], list[MemoryEntry], list[dict[str, object]]]:
        entries, audit, raw_target = self._read_entries_and_audit(memory_id)
        target_matches = [entry for entry in entries if entry.id == memory_id]
        if len(target_matches) > 1:
            raise ProjectMemoryDeletionError("deletion_unavailable")
        target = target_matches[0] if target_matches else None
        audit_matches = [record for record in audit if record.get("entry_id") == memory_id]
        invalid_audit = any(
            not isinstance(record.get("entry_id"), str) for record in audit
        )
        backlinks = [entry for entry in entries if memory_id in entry.related_to]
        backlink_count = sum(entry.related_to.count(memory_id) for entry in backlinks)
        if target is None and not audit_matches and not backlinks and fence is None:
            raise ProjectMemoryDeletionError("deletion_target_not_found")

        diagnostics: list[dict[str, str]] = []
        category = "unknown"
        tier = "unknown"
        lifecycle = "unknown"
        approval = "unknown"
        if target is not None:
            category = target.category
            tier = target.tier.value
            lifecycle = target.lifecycle_status
            approval = target.approval_status
            raw_category = raw_target.get("category", category) if raw_target else category
            raw_tier = raw_target.get("tier", tier) if raw_target else tier
            raw_lifecycle = (
                raw_target.get("lifecycle_status", lifecycle)
                if raw_target
                else lifecycle
            )
            raw_approval = (
                raw_target.get("approval_status", approval)
                if raw_target
                else approval
            )
            if (
                raw_category not in _PUBLIC_CATEGORIES
                or raw_tier not in {item.value for item in MemoryTier}
                or raw_lifecycle not in _LIFECYCLE_STATUSES
                or raw_approval not in _APPROVAL_STATUSES
            ):
                diagnostics.append({"code": "memory_metadata_invalid"})
                category = tier = lifecycle = approval = "unknown"
        if invalid_audit:
            diagnostics.append({"code": "memory_audit_invalid"})

        status = "unavailable" if diagnostics else "partial" if fence else "ready"
        canonical = {
            "target": _hash_payload(target.to_dict()) if target is not None else None,
            "audit": sorted(_hash_payload(record) for record in audit_matches),
            "backlinks": sorted(
                [
                    entry.id,
                    entry.related_to.count(memory_id),
                    _hash_payload(entry.to_dict()),
                ]
                for entry in backlinks
            ),
            "diagnostics": [item["code"] for item in diagnostics],
            "fence": ["absent"],
        }
        revision = (
            fence.deletion_revision
            if fence is not None
            else "delrev_" + _hash_payload(canonical)
        )
        preview = {
            "schemaVersion": 1,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-write",
            "kind": "project-memory",
            "target": {
                "memoryId": memory_id,
                "scope": "project",
                "category": category,
                "tier": tier,
                "lifecycleStatus": lifecycle,
                "approvalStatus": approval,
            },
            "status": status,
            "deletionRevision": revision,
            "affected": {
                "entries": int(target is not None),
                "approvalAuditRecords": len(audit_matches),
                "backlinks": backlink_count,
            },
            "blockers": [],
            "diagnostics": diagnostics,
        }
        return preview, entries, audit

    def _receipt_preview(
        self, memory_id: str, receipt: DeletionRecord
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-write",
            "kind": "project-memory",
            "target": {
                "memoryId": memory_id,
                "scope": "project",
                "category": "unknown",
                "tier": "unknown",
                "lifecycleStatus": "unknown",
                "approvalStatus": "unknown",
            },
            "status": "completed",
            "deletionRevision": receipt.deletion_revision,
            "affected": {"entries": 0, "approvalAuditRecords": 0, "backlinks": 0},
            "blockers": [],
            "diagnostics": [],
        }

    def snapshot(self, target_id: str) -> dict[str, object]:
        memory_id = self._validate_id(target_id)
        try:
            receipt = self._ledger.read_receipt("project-memory", memory_id)
            if receipt is not None:
                return self._receipt_preview(memory_id, receipt)
            fence = self._ledger.read_fence("project-memory", memory_id)
            preview, _, _ = self._plan(memory_id, fence=fence)
            return preview
        except ProjectMemoryDeletionError:
            raise
        except (DeletionStoreBusy, MemoryStoreBusy) as error:
            raise ProjectMemoryDeletionError("deletion_store_busy") from error
        except (
            DeletionStoreUnavailable,
            MemoryApprovalError,
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            raise ProjectMemoryDeletionError("deletion_unavailable") from error
        except BaseException as error:
            raise ProjectMemoryDeletionError("deletion_unavailable") from error

    def _result(
        self,
        memory_id: str,
        revision: str,
        *,
        status: str,
        deleted: dict[str, int],
        remaining: dict[str, int],
        diagnostic: str | None = None,
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-write",
            "kind": "project-memory",
            "target": {"memoryId": memory_id, "scope": "project"},
            "status": status,
            "deletionRevision": revision,
            "deleted": deleted,
            "remaining": remaining,
            "diagnostics": ([{"code": diagnostic}] if diagnostic else []),
        }

    def delete(self, target_id: str, deletion_revision: str) -> dict[str, object]:
        memory_id = self._validate_id(target_id)
        requested = self._validate_revision(deletion_revision)
        existing_fence = False
        try:
            with self._ledger.coordination():
                receipt = self._ledger.read_receipt("project-memory", memory_id)
                if receipt is not None:
                    if not hmac.compare_digest(receipt.deletion_revision, requested):
                        raise ProjectMemoryDeletionError("deletion_target_not_found")
                    return self._result(
                        memory_id,
                        requested,
                        status="already_absent",
                        deleted={"entries": 0, "approvalAuditRecords": 0, "backlinks": 0},
                        remaining={"entries": 0, "approvalAuditRecords": 0, "backlinks": 0},
                    )
                fence = self._ledger.read_fence("project-memory", memory_id)
                existing_fence = fence is not None
                if fence is None:
                    preview, _, _ = self._plan(memory_id)
                    if not hmac.compare_digest(str(preview["deletionRevision"]), requested):
                        raise ProjectMemoryDeletionError("deletion_revision_stale")
                    if preview["status"] != "ready":
                        raise ProjectMemoryDeletionError("deletion_unavailable")
                    fence = self._ledger.start("project-memory", memory_id, requested)
                elif not hmac.compare_digest(fence.deletion_revision, requested):
                    raise ProjectMemoryDeletionError("deletion_revision_stale")

            manager = MemoryManager(
                project_root=self.workspace,
                store_timeout=self._store_timeout,
                readonly_load=True,
            )
            deleted = {"entries": 0, "approvalAuditRecords": 0, "backlinks": 0}

            def commit() -> dict[str, object]:
                nonlocal deleted
                current_fence = self._ledger.read_fence("project-memory", memory_id)
                if current_fence is None:
                    with self._ledger.coordination():
                        completed = self._ledger.read_receipt(
                            "project-memory", memory_id
                        )
                    if completed is not None and hmac.compare_digest(
                        completed.deletion_revision, requested
                    ):
                        return self._result(
                            memory_id,
                            requested,
                            status="already_absent",
                            deleted={
                                "entries": 0,
                                "approvalAuditRecords": 0,
                                "backlinks": 0,
                            },
                            remaining={
                                "entries": 0,
                                "approvalAuditRecords": 0,
                                "backlinks": 0,
                            },
                        )
                current, _, _ = self._plan(
                    memory_id,
                    fence=current_fence if existing_fence else None,
                )
                if not existing_fence and not hmac.compare_digest(
                    str(current["deletionRevision"]), requested
                ):
                    with self._ledger.coordination():
                        self._ledger.abandon("project-memory", memory_id)
                    raise ProjectMemoryDeletionError("deletion_revision_stale")
                if current["status"] == "unavailable":
                    raise ProjectMemoryDeletionError("deletion_unavailable")

                memory_file = manager.memories[MemoryScope.PROJECT]
                records = manager.approval_audit[MemoryScope.PROJECT]
                kept_records = [
                    record for record in records if record.get("entry_id") != memory_id
                ]
                deleted["approvalAuditRecords"] = len(records) - len(kept_records)
                manager.approval_audit[MemoryScope.PROJECT] = kept_records
                for entry in memory_file.entries:
                    occurrences = entry.related_to.count(memory_id)
                    if occurrences:
                        deleted["backlinks"] += occurrences
                        entry.related_to = [
                            related for related in entry.related_to if related != memory_id
                        ]
                deleted["entries"] = int(memory_file.delete_entry(memory_id))
                if any(int(value) for value in deleted.values()):
                    # memory.json is the single authority for entries and
                    # approval audit. Commit both mutations together; the
                    # Markdown/audit files are best-effort projections only.
                    manager._save_scope(MemoryScope.PROJECT)

                verification, _, _ = self._plan(memory_id, fence=current_fence)
                remaining = dict(verification["affected"])
                if any(int(value) for value in remaining.values()):
                    return self._result(
                        memory_id,
                        requested,
                        status="partial",
                        deleted=deleted,
                        remaining=remaining,
                        diagnostic="deletion_retry_required",
                    )
                with self._ledger.coordination():
                    self._ledger.complete("project-memory", memory_id, requested)
                return self._result(
                    memory_id,
                    requested,
                    status="completed",
                    deleted=deleted,
                    remaining={"entries": 0, "approvalAuditRecords": 0, "backlinks": 0},
                )

            return manager.coordinated_write((MemoryScope.PROJECT,), commit)
        except ProjectMemoryDeletionError:
            raise
        except (DeletionStoreBusy, MemoryStoreBusy) as error:
            raise ProjectMemoryDeletionError("deletion_store_busy") from error
        except (MemoryStoreConflict,) as error:
            raise ProjectMemoryDeletionError("deletion_write_conflict") from error
        except (
            DeletionStoreUnavailable,
            MemoryApprovalError,
            MemoryStoreUnavailable,
        ) as error:
            raise ProjectMemoryDeletionError("deletion_unavailable") from error
        except BaseException:
            if self._ledger.read_fence("project-memory", memory_id) is not None:
                try:
                    remaining_preview, _, _ = self._plan(
                        memory_id,
                        fence=self._ledger.read_fence("project-memory", memory_id),
                    )
                    remaining = dict(remaining_preview["affected"])
                except BaseException:
                    remaining = {"entries": 1, "approvalAuditRecords": 1, "backlinks": 1}
                return self._result(
                    memory_id,
                    requested,
                    status="partial",
                    deleted={"entries": 0, "approvalAuditRecords": 0, "backlinks": 0},
                    remaining=remaining,
                    diagnostic="deletion_retry_required",
                )
            raise ProjectMemoryDeletionError("deletion_failed")


__all__ = [
    "ProjectMemoryDeletionAuthority",
    "ProjectMemoryDeletionError",
]
