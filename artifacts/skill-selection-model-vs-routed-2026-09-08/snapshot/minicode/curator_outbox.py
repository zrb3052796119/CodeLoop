"""Cross-entrypoint recovery for workspace-scoped curator outboxes.

CLI Runs and Dashboard Turns are separate durable producers for one shared
curator scheduler.  Recovery therefore cannot be entrypoint-local: a bounded
ring filled with receipts from an entrypoint that is never opened again would
otherwise block every other producer forever.

These helpers carry no task, prompt, Memory, or model content.  Callers receive
only a boolean exhaustive-proof result and must defer newer scheduling work on
``False``.
"""

from __future__ import annotations

from typing import Any

from minicode.curator_schedule import curator_completion_id

_MAX_RECOVERY_PAGES = 12
_PAGE_LIMIT = 1_000


def turn_recovery_scan_budget(turn_store: Any) -> int:
    """Return the store's explicit one-pass Turn recovery bound.

    ``ConversationTurnStore.scan_limit`` bounds the number of durable records
    that one scan can classify.  Consumers use the same bound for unique
    recovery records, instead of an unrelated fixed page count.  Compatibility
    stores without that contract retain one bounded page of work.
    """

    value = getattr(turn_store, "scan_limit", _PAGE_LIMIT)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return _PAGE_LIMIT
    return value


def reconcile_run_curator_outbox(
    journal: Any,
    schedule: Any,
) -> bool:
    """Drain the durable CLI Run outbox through the shared scheduler."""

    scanner = getattr(journal, "scan_pending_curator_completions", None)
    beginner = getattr(journal, "begin_curator_completion_delivery", None)
    acknowledger = getattr(journal, "acknowledge_curator_completion", None)
    settler = getattr(journal, "settle_curator_completion", None)
    if not all(
        callable(method)
        for method in (scanner, beginner, acknowledger, settler)
    ):
        # Compatibility journals have no durable curator producer and cannot
        # contribute workspace pins.
        return True
    try:
        with schedule.delivery_fence():
            for _ in range(_MAX_RECOVERY_PAGES):
                scan = scanner(limit=_PAGE_LIMIT)
                release_items = tuple(getattr(scan, "release_items", ()))
                delivery_items = tuple(getattr(scan, "items", ()))
                if not release_items and not delivery_items:
                    return bool(getattr(scan, "exhaustive", False))
                progressed = False
                for run_id, completion_id in release_items:
                    schedule.acknowledge_completion(completion_id)
                    if settler(run_id, completion_id) is not True:
                        return False
                    progressed = True
                for run_id, completion_id in delivery_items:
                    if beginner(run_id, completion_id) is not True:
                        return False
                    schedule.record_completion(
                        completion_id,
                        pin=True,
                        promote_existing=True,
                    )
                    if acknowledger(run_id, completion_id) is not True:
                        return False
                    schedule.acknowledge_completion(completion_id)
                    if settler(run_id, completion_id) is not True:
                        return False
                    progressed = True
                if not progressed:
                    return False
    except Exception:
        return False
    return False


def reconcile_turn_curator_outbox(
    turn_store: Any,
    schedule: Any,
) -> bool:
    """Drain recoverable Dashboard Turn receipts through the scheduler.

    A completed Turn is itself proof that Session commit succeeded.  A
    committing Turn may be recovered here only when the scheduler still holds
    its pin (delivery happened) or the Turn already durably recorded the
    producer acknowledgement.  Other committing records remain content
    recovery concerns and do not block unrelated scheduling.
    """

    scanner = getattr(turn_store, "curator_receipt_recovery_records", None)
    mark_recorded = getattr(turn_store, "mark_curator_receipt_recorded", None)
    mark_settled = getattr(turn_store, "mark_curator_receipt_settled", None)
    if not all(callable(method) for method in (scanner, mark_recorded, mark_settled)):
        return True
    try:
        with schedule.delivery_fence():
            scan_budget = turn_recovery_scan_budget(turn_store)
            observed_turn_ids: set[str] = set()
            while True:
                remaining = scan_budget - len(observed_turn_ids)
                scan = scanner(
                    limit=min(
                        _PAGE_LIMIT,
                        max(1, remaining),
                    )
                )
                records = tuple(getattr(scan, "items", ()))
                if not records:
                    return bool(getattr(scan, "exhaustive", False))
                page_turn_ids = {
                    turn_id
                    for turn_id in (
                        getattr(record, "turn_id", None)
                        for record in records
                    )
                    if isinstance(turn_id, str)
                }
                newly_observed = page_turn_ids - observed_turn_ids
                if len(newly_observed) > remaining:
                    return False
                observed_turn_ids.update(page_turn_ids)
                pinned = frozenset(schedule.snapshot().pinned_completion_ids)
                progressed = False
                actionable = False
                for record in records:
                    if bool(
                        getattr(record, "curator_receipt_settled", False)
                    ):
                        # Session-to-Turn content recovery is independent of
                        # this already-finished scheduler handshake.
                        continue
                    completion_id = curator_completion_id(
                        getattr(record, "run_id", None),
                        getattr(record, "turn_id", None),
                    )
                    if completion_id is None:
                        return False
                    recorded = bool(
                        getattr(record, "curator_receipt_recorded", False)
                    )
                    status = getattr(record, "status", None)
                    if recorded:
                        actionable = True
                    elif status == "completed" or completion_id in pinned:
                        actionable = True
                        schedule.record_completion(
                            completion_id,
                            pin=True,
                            promote_existing=True,
                        )
                        record = mark_recorded(record.turn_id)
                    else:
                        # A committing, unrecorded Turn with no scheduler pin
                        # has not changed scheduling state. Session recovery is
                        # owned by ConversationTurnService.
                        continue
                    schedule.acknowledge_completion(completion_id)
                    settled = mark_settled(record.turn_id)
                    if not bool(
                        getattr(settled, "curator_receipt_settled", False)
                    ):
                        return False
                    progressed = True
                if not actionable:
                    unsafe = set(getattr(scan, "diagnostics", ())) - {
                        "curator_receipt_page_limited"
                    }
                    return not unsafe
                if not progressed:
                    return False
                if not newly_observed:
                    # A non-exhaustive scanner that repeats an unchanged page
                    # cannot prove forward progress. Fail closed instead of
                    # spinning inside the configured recovery bound.
                    return False
    except Exception:
        return False
    return False


__all__ = [
    "reconcile_run_curator_outbox",
    "reconcile_turn_curator_outbox",
    "turn_recovery_scan_budget",
]
