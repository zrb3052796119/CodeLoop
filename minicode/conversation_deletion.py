"""Workspace-scoped authority for deleting a complete saved conversation."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from minicode.conversation_turn_store import ConversationTurnStore, TurnStoreError
from minicode.deletion_store import (
    DeletionLedger,
    DeletionRecord,
    DeletionStoreBusy,
    DeletionStoreUnavailable,
)
from minicode.run_journal import RunJournal, RunJournalStorageError
from minicode.session import (
    delete_session,
    session_deletion_snapshot,
)


DELETION_REVISION_RE = re.compile(r"delrev_[0-9a-f]{64}")
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class ConversationDeletionError(RuntimeError):
    """One fixed-vocabulary conversation deletion failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ConversationDeletionError("deletion_unavailable")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _revision(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "delrev_" + hashlib.sha256(encoded).hexdigest()


class ConversationDeletionAuthority:
    """Preview and delete one conversation behind a two-method interface.

    Linearization happens when a content-free deletion fence is atomically
    installed under the Workspace ledger lock.  Linked writers consult that
    same fence before they can save the Session or add a Turn/Run association.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path,
        clock: Callable[[], datetime] = _utc_now,
        store_timeout: float = 5.0,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve(strict=False)
        self._clock = clock
        self._turns = ConversationTurnStore(self.workspace, data_dir=self.data_dir)
        self._runs = RunJournal(self.workspace, data_dir=self.data_dir)
        self._ledger = DeletionLedger(
            self.workspace,
            data_dir=self.data_dir,
            clock=clock,
            timeout=store_timeout,
        )

    @staticmethod
    def _validate_id(session_id: object) -> str:
        if not isinstance(session_id, str) or SESSION_ID_RE.fullmatch(session_id) is None:
            raise ConversationDeletionError("invalid_id")
        return session_id

    @staticmethod
    def _validate_revision(deletion_revision: object) -> str:
        if (
            not isinstance(deletion_revision, str)
            or DELETION_REVISION_RE.fullmatch(deletion_revision) is None
        ):
            raise ConversationDeletionError("invalid_revision")
        return deletion_revision

    def _plan(
        self,
        session_id: str,
        *,
        fence: DeletionRecord | None = None,
    ) -> tuple[dict[str, object], object, object, object]:
        session = session_deletion_snapshot(
            session_id,
            self.workspace,
            allow_unowned_orphans=fence is not None,
        )
        turns = self._turns.deletion_snapshot(session_id)
        runs = self._runs.deletion_snapshot(session_id)
        if (
            fence is None
            and
            not session.present
            and not turns.terminal
            and not turns.active
            and not runs.terminal
            and not runs.active
        ):
            raise ConversationDeletionError("deletion_target_not_found")
        blockers = [
            *([{"code": "active_turn"}] if turns.active else []),
            *([{"code": "active_run"}] if runs.active else []),
        ]
        diagnostic_codes = sorted(
            {*session.diagnostics, *turns.diagnostics, *runs.diagnostics}
        )
        diagnostics = [{"code": code} for code in diagnostic_codes]
        if diagnostics:
            status = "unavailable"
        elif blockers:
            status = "busy"
        elif fence is not None:
            status = "partial"
        else:
            status = "ready"
        canonical = {
            "session": {
                "base": session.base_present,
                "index": session.index_present,
                "deltas": session.delta_count,
                "generation": session.generation,
            },
            "turns": [
                [item.turn_id, item.status, item.updated_at, item.run_id]
                for item in (*turns.terminal, *turns.active)
            ],
            "runs": [
                [item.id, item.status, item.updated_at, item.last_sequence]
                for item in (*runs.terminal, *runs.active)
            ],
            "diagnostics": diagnostic_codes,
            "fence": (
                ["in_progress", fence.deletion_revision]
                if fence is not None
                else ["absent"]
            ),
        }
        preview = {
            "schemaVersion": 1,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-write",
            "kind": "conversation",
            "target": {"sessionId": session_id},
            "status": status,
            "deletionRevision": (
                fence.deletion_revision if fence is not None else _revision(canonical)
            ),
            "affected": {
                "sessions": int(session.present),
                "turns": len(turns.terminal),
                "runs": len(runs.terminal),
            },
            "blockers": blockers,
            "diagnostics": diagnostics,
        }
        return preview, session, turns, runs

    def _receipt_preview(
        self,
        session_id: str,
        receipt: DeletionRecord,
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-write",
            "kind": "conversation",
            "target": {"sessionId": session_id},
            "status": "completed",
            "deletionRevision": receipt.deletion_revision,
            "affected": {"sessions": 0, "turns": 0, "runs": 0},
            "blockers": [],
            "diagnostics": [],
        }

    def snapshot(self, target_id: str) -> dict[str, object]:
        session_id = self._validate_id(target_id)
        try:
            receipt = self._ledger.read_receipt("conversation", session_id)
            if receipt is not None:
                return self._receipt_preview(session_id, receipt)
            fence = self._ledger.read_fence("conversation", session_id)
            preview, _, _, _ = self._plan(session_id, fence=fence)
            return preview
        except ConversationDeletionError:
            raise
        except DeletionStoreBusy as error:
            raise ConversationDeletionError("deletion_store_busy") from error
        except DeletionStoreUnavailable as error:
            raise ConversationDeletionError("deletion_unavailable") from error
        except BaseException as error:
            raise ConversationDeletionError("deletion_unavailable") from error

    def _result(
        self,
        session_id: str,
        deletion_revision: str,
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
            "kind": "conversation",
            "target": {"sessionId": session_id},
            "status": status,
            "deletionRevision": deletion_revision,
            "deleted": deleted,
            "remaining": remaining,
            "diagnostics": ([{"code": diagnostic}] if diagnostic else []),
        }

    def delete(self, target_id: str, deletion_revision: str) -> dict[str, object]:
        session_id = self._validate_id(target_id)
        requested_revision = self._validate_revision(deletion_revision)
        try:
            with self._ledger.coordination():
                receipt = self._ledger.read_receipt("conversation", session_id)
                if receipt is not None:
                    if not hmac.compare_digest(
                        receipt.deletion_revision, requested_revision
                    ):
                        raise ConversationDeletionError(
                            "deletion_target_not_found"
                        )
                    return self._result(
                        session_id,
                        requested_revision,
                        status="already_absent",
                        deleted={"sessions": 0, "turns": 0, "runs": 0},
                        remaining={"sessions": 0, "turns": 0, "runs": 0},
                    )

                fence = self._ledger.read_fence("conversation", session_id)
                preview, _, _, _ = self._plan(session_id, fence=fence)
                if not hmac.compare_digest(
                    str(preview["deletionRevision"]), requested_revision
                ):
                    raise ConversationDeletionError("deletion_revision_stale")
                if preview["status"] == "busy":
                    raise ConversationDeletionError("deletion_target_busy")
                if preview["status"] not in {"ready", "partial"}:
                    raise ConversationDeletionError("deletion_unavailable")
                installed = self._ledger.start(
                    "conversation", session_id, requested_revision
                )
                if installed.deletion_revision != requested_revision:
                    raise ConversationDeletionError("deletion_revision_stale")

            deleted = {"sessions": 0, "turns": 0, "runs": 0}
            try:
                deleted["turns"] = self._turns.delete_terminal_for_session(session_id)
                deleted["runs"] = self._runs.delete_terminal_for_session(session_id)
                deleted["sessions"] = int(delete_session(session_id))
                fence = self._ledger.read_fence("conversation", session_id)
                try:
                    verification, _, _, _ = self._plan(session_id, fence=fence)
                except ConversationDeletionError as error:
                    if error.code != "deletion_target_not_found":
                        raise
                    verification = None
                if verification is not None and (
                    any(int(value) for value in verification["affected"].values())
                    or verification["blockers"]
                    or verification["diagnostics"]
                ):
                    return self._result(
                        session_id,
                        requested_revision,
                        status="partial",
                        deleted=deleted,
                        remaining=dict(verification["affected"]),
                        diagnostic="deletion_retry_required",
                    )
            except (TurnStoreError, RunJournalStorageError, OSError):
                try:
                    fence = self._ledger.read_fence("conversation", session_id)
                    remaining_preview, _, _, _ = self._plan(session_id, fence=fence)
                    remaining = dict(remaining_preview["affected"])
                except BaseException:
                    remaining = {"sessions": 1, "turns": 1, "runs": 1}
                return self._result(
                    session_id,
                    requested_revision,
                    status="partial",
                    deleted=deleted,
                    remaining=remaining,
                    diagnostic="deletion_retry_required",
                )

            with self._ledger.coordination():
                self._ledger.complete(
                    "conversation", session_id, requested_revision
                )
            return self._result(
                session_id,
                requested_revision,
                status="completed",
                deleted=deleted,
                remaining={"sessions": 0, "turns": 0, "runs": 0},
            )
        except ConversationDeletionError:
            raise
        except DeletionStoreBusy as error:
            raise ConversationDeletionError("deletion_store_busy") from error
        except DeletionStoreUnavailable as error:
            raise ConversationDeletionError("deletion_unavailable") from error
        except BaseException as error:
            raise ConversationDeletionError("deletion_failed") from error


__all__ = [
    "ConversationDeletionAuthority",
    "ConversationDeletionError",
    "DELETION_REVISION_RE",
]
