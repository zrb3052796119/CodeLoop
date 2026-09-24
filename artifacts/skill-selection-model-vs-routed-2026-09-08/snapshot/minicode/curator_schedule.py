"""Durable, workspace-scoped scheduling authority for Memory curation.

The Agent runtime is rebuilt for every top-level Turn, so an in-memory task
counter cannot implement an every-N-tasks schedule.  This store owns the small
piece of durable coordination needed by the Pipeline:

* replay-safe task-completion receipts;
* one cross-process active claim;
* success-only pending-task consumption; and
* bounded retry state after failures or cancellation.

The file is content-free.  It contains hashes and counters only; task prompts,
Memory text, and model output never enter this authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from minicode.memory_store import MemoryStoreCoordinator

_SCHEMA_VERSION = 3
_READABLE_SCHEMA_VERSIONS = frozenset({1, 2, _SCHEMA_VERSION})
# A worst-case authority can temporarily contain the same 12,000 content-free
# identifiers in both the recent-receipt window and the pinned subset.  Two
# MiB keeps that representation bounded without making a valid all-pinned
# state unreadable.
MAX_CURATOR_SCHEDULE_BYTES = 2 * 1024 * 1024
_MAX_COUNTER = 1_000_000_000
MAX_CURATOR_INTERVAL_TASKS = 128
# Keep deduplication evidence for at least as long as the largest built-in
# durable producer retains replayable completion records.  Dashboard Turns
# retain 10,000 records (RunJournal retains fewer), so a late recovery cannot
# become a new completion merely because the scheduling interval is small.
# This is deliberately larger than the default retained Turn (10k) plus Run
# (1k) producer horizons.  Delivery is still explicitly documented and tested
# as bounded idempotency; outbox consumers fail closed on an uncertain ack.
_MAX_RECENT_COMPLETIONS = 12_000
_DEFAULT_LEASE_SECONDS = 15 * 60.0
_MAX_LEASE_SECONDS = 24 * 60 * 60.0
_MAX_LEASE_MS = int(_MAX_LEASE_SECONDS * 1000)
_FAILURE_BACKOFF_BASE_SECONDS = 5.0
_FAILURE_BACKOFF_MAX_SECONDS = 5 * 60.0
_CANCELLATION_BACKOFF_SECONDS = 5.0
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
_MAX_RETRY_FUTURE_MS = int(
    (_FAILURE_BACKOFF_MAX_SECONDS * 1000) + _MAX_CLOCK_SKEW_MS
)
_HEX_32_RE = re.compile(r"[0-9a-f]{32}")
_HEX_64_RE = re.compile(r"[0-9a-f]{64}")
_RUN_ID_RE = re.compile(r"run_[0-9a-f]{32}")
_TURN_ID_RE = re.compile(r"turn_[0-9a-f]{32}")


class CuratorScheduleError(RuntimeError):
    """Base class for durable curator scheduling failures."""


class CuratorScheduleCorrupt(CuratorScheduleError):
    """The existing schedule authority is malformed or unsafe."""


class CuratorClaimLost(CuratorScheduleError):
    """A curator worker can no longer prove ownership of its execution lease."""


@dataclass(frozen=True, slots=True)
class CuratorClaim:
    claim_id: str
    owner_id: str
    claimed_at_ms: int
    lease_until_ms: int
    consume_task_count: int


@dataclass(frozen=True, slots=True)
class CuratorScheduleState:
    generation: int = 0
    pending_task_count: int = 0
    recent_completion_ids: tuple[str, ...] = ()
    # A producer pins a receipt after its local write-ahead barrier and keeps
    # it pinned until the producer has durably acknowledged delivery. Pinned
    # receipts cannot be evicted by unrelated CLI or Dashboard completions.
    pinned_completion_ids: tuple[str, ...] = ()
    active_claim: CuratorClaim | None = None
    retry_not_before_ms: int = 0
    consecutive_failures: int = 0
    last_attempt_at_ms: int = 0
    last_completed_at_ms: int = 0
    completed_cycle_count: int = 0
    # Read metadata only; it is never emitted as an additional JSON field and
    # does not participate in semantic state equality.  It lets the first
    # current-version mutation rewrite a v1 authority as v2 even when the
    # completion receipt is a replay or the schedule is below threshold.
    source_schema_version: int = field(
        default=_SCHEMA_VERSION,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class CuratorClaimDecision:
    state: CuratorScheduleState
    claim: CuratorClaim | None = None
    replayed_completion: bool = False
    reason: str = "below_threshold"


def curator_completion_id(
    run_id: object,
    turn_id: object | None = None,
) -> str | None:
    """Derive a content-free stable completion receipt from root identities."""

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        return None
    normalized_turn: str | None
    if turn_id is None:
        normalized_turn = None
    elif (
        isinstance(turn_id, str)
        and _TURN_ID_RE.fullmatch(turn_id) is not None
    ):
        normalized_turn = turn_id
    else:
        return None
    payload = json.dumps(
        {
            "schema": "curator-completion-v1",
            "runId": run_id,
            "turnId": normalized_turn,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _bounded_counter(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_COUNTER
    ):
        raise CuratorScheduleCorrupt(f"invalid {field}")
    return value


def _now_ms(value: float | None) -> int:
    candidate = time.time() if value is None else value
    if (
        isinstance(candidate, bool)
        or not isinstance(candidate, (int, float))
        or not math.isfinite(float(candidate))
        or float(candidate) < 0
    ):
        raise ValueError("now must be a finite non-negative timestamp")
    milliseconds = int(float(candidate) * 1000)
    # Epoch milliseconds already exceed the counter bound, so timestamps use
    # a wider, separately bounded domain.
    if milliseconds > 99_999_999_999_999:
        raise ValueError("now is out of range")
    return milliseconds


def _timestamp_ms(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > 99_999_999_999_999
    ):
        raise CuratorScheduleCorrupt(f"invalid {field}")
    return value


def _validate_interval(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_CURATOR_INTERVAL_TASKS
    ):
        raise ValueError(
            "interval exceeds the supported curator scheduling window"
        )
    return value


def _validate_lease_seconds(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 1.0 <= float(value) <= _MAX_LEASE_SECONDS
    ):
        raise ValueError("lease_seconds is out of range")
    return float(value)


def _decode_claim(value: object) -> CuratorClaim | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "claimId",
        "ownerId",
        "claimedAtMs",
        "leaseUntilMs",
        "consumeTaskCount",
    }:
        raise CuratorScheduleCorrupt("invalid activeClaim")
    claim_id = value.get("claimId")
    owner_id = value.get("ownerId")
    if not isinstance(claim_id, str) or _HEX_32_RE.fullmatch(claim_id) is None:
        raise CuratorScheduleCorrupt("invalid activeClaim.claimId")
    if not isinstance(owner_id, str) or _HEX_32_RE.fullmatch(owner_id) is None:
        raise CuratorScheduleCorrupt("invalid activeClaim.ownerId")
    claimed_at = _timestamp_ms(value.get("claimedAtMs"), field="claimedAtMs")
    lease_until = _timestamp_ms(value.get("leaseUntilMs"), field="leaseUntilMs")
    if (
        lease_until <= claimed_at
        or lease_until - claimed_at > _MAX_LEASE_MS
    ):
        raise CuratorScheduleCorrupt("invalid activeClaim lease")
    return CuratorClaim(
        claim_id=claim_id,
        owner_id=owner_id,
        claimed_at_ms=claimed_at,
        lease_until_ms=lease_until,
        consume_task_count=_bounded_counter(
            value.get("consumeTaskCount"),
            field="consumeTaskCount",
        ),
    )


def _decode_state(payload: object) -> CuratorScheduleState:
    legacy_expected = {
        "schemaVersion",
        "generation",
        "pendingTaskCount",
        "recentCompletionIds",
        "activeClaim",
        "retryNotBeforeMs",
        "consecutiveFailures",
        "lastAttemptAtMs",
        "lastCompletedAtMs",
        "completedCycleCount",
    }
    if not isinstance(payload, dict):
        raise CuratorScheduleCorrupt("invalid schedule schema")
    schema_version = payload.get("schemaVersion")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in _READABLE_SCHEMA_VERSIONS
    ):
        raise CuratorScheduleCorrupt("unsupported schedule schema")
    expected = (
        legacy_expected | {"pinnedCompletionIds"}
        if schema_version == _SCHEMA_VERSION
        else legacy_expected
    )
    if set(payload) != expected:
        raise CuratorScheduleCorrupt("invalid schedule schema")
    raw_receipts = payload.get("recentCompletionIds")
    if not isinstance(raw_receipts, list) or len(raw_receipts) > _MAX_RECENT_COMPLETIONS:
        raise CuratorScheduleCorrupt("invalid recentCompletionIds")
    receipts: list[str] = []
    seen_receipts: set[str] = set()
    for receipt in raw_receipts:
        if not isinstance(receipt, str) or _HEX_64_RE.fullmatch(receipt) is None:
            raise CuratorScheduleCorrupt("invalid completion receipt")
        if receipt in seen_receipts:
            raise CuratorScheduleCorrupt("duplicate completion receipt")
        seen_receipts.add(receipt)
        receipts.append(receipt)
    raw_pinned = payload.get("pinnedCompletionIds", [])
    if not isinstance(raw_pinned, list) or len(raw_pinned) > _MAX_RECENT_COMPLETIONS:
        raise CuratorScheduleCorrupt("invalid pinnedCompletionIds")
    pinned: list[str] = []
    seen_pinned: set[str] = set()
    for receipt in raw_pinned:
        if (
            not isinstance(receipt, str)
            or _HEX_64_RE.fullmatch(receipt) is None
            or receipt in seen_pinned
            or receipt not in seen_receipts
        ):
            raise CuratorScheduleCorrupt("invalid pinned completion receipt")
        seen_pinned.add(receipt)
        pinned.append(receipt)
    state = CuratorScheduleState(
        generation=_bounded_counter(payload.get("generation"), field="generation"),
        pending_task_count=_bounded_counter(
            payload.get("pendingTaskCount"),
            field="pendingTaskCount",
        ),
        recent_completion_ids=tuple(receipts),
        pinned_completion_ids=tuple(pinned),
        active_claim=_decode_claim(payload.get("activeClaim")),
        retry_not_before_ms=_timestamp_ms(
            payload.get("retryNotBeforeMs"),
            field="retryNotBeforeMs",
        ),
        consecutive_failures=_bounded_counter(
            payload.get("consecutiveFailures"),
            field="consecutiveFailures",
        ),
        last_attempt_at_ms=_timestamp_ms(
            payload.get("lastAttemptAtMs"),
            field="lastAttemptAtMs",
        ),
        last_completed_at_ms=_timestamp_ms(
            payload.get("lastCompletedAtMs"),
            field="lastCompletedAtMs",
        ),
        completed_cycle_count=_bounded_counter(
            payload.get("completedCycleCount"),
            field="completedCycleCount",
        ),
        source_schema_version=schema_version,
    )
    if (
        state.active_claim is not None
        and state.active_claim.consume_task_count > state.pending_task_count
    ):
        raise CuratorScheduleCorrupt("claim consumes unavailable tasks")
    if (
        state.active_claim is not None
        and state.active_claim.claimed_at_ms != state.last_attempt_at_ms
    ):
        raise CuratorScheduleCorrupt("claim attempt timestamp mismatch")
    return state


def _validate_temporal_state(
    state: CuratorScheduleState,
    *,
    now_ms: int,
) -> CuratorScheduleState:
    """Reject clocks that could freeze scheduling for an unbounded period."""

    latest_clock = now_ms + _MAX_CLOCK_SKEW_MS
    if state.last_attempt_at_ms > latest_clock:
        raise CuratorScheduleCorrupt("lastAttemptAtMs is implausibly future-dated")
    if state.last_completed_at_ms > latest_clock:
        raise CuratorScheduleCorrupt("lastCompletedAtMs is implausibly future-dated")
    if state.retry_not_before_ms > now_ms + _MAX_RETRY_FUTURE_MS:
        raise CuratorScheduleCorrupt("retryNotBeforeMs is implausibly future-dated")
    claim = state.active_claim
    if claim is not None and claim.claimed_at_ms > latest_clock:
        raise CuratorScheduleCorrupt("activeClaim is implausibly future-dated")
    return state


def _encode_claim(claim: CuratorClaim | None) -> dict[str, object] | None:
    if claim is None:
        return None
    return {
        "claimId": claim.claim_id,
        "ownerId": claim.owner_id,
        "claimedAtMs": claim.claimed_at_ms,
        "leaseUntilMs": claim.lease_until_ms,
        "consumeTaskCount": claim.consume_task_count,
    }


def _encode_state(state: CuratorScheduleState) -> dict[str, object]:
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "generation": state.generation,
        "pendingTaskCount": state.pending_task_count,
        "recentCompletionIds": list(state.recent_completion_ids),
        "pinnedCompletionIds": list(state.pinned_completion_ids),
        "activeClaim": _encode_claim(state.active_claim),
        "retryNotBeforeMs": state.retry_not_before_ms,
        "consecutiveFailures": state.consecutive_failures,
        "lastAttemptAtMs": state.last_attempt_at_ms,
        "lastCompletedAtMs": state.last_completed_at_ms,
        "completedCycleCount": state.completed_cycle_count,
    }


def validate_curator_schedule_payload(
    payload: object,
) -> CuratorScheduleState:
    """Strict read-only validator shared with persistence health reporting."""

    return _validate_temporal_state(
        _decode_state(payload),
        now_ms=_now_ms(None),
    )


class CuratorScheduleStore:
    """Atomic workspace authority for curator task receipts and claims."""

    def __init__(
        self,
        workspace_path: str | Path,
        *,
        lease_seconds: float = _DEFAULT_LEASE_SECONDS,
        owner_id: str | None = None,
        lock_timeout: float = 5.0,
    ) -> None:
        workspace = Path(workspace_path).expanduser().resolve()
        self._root = workspace / ".mini-code-memory"
        self._path = self._root / "curator_schedule.json"
        # Reuse the audited process/advisory-lock implementation while keeping
        # this authority independent from the Memory content-store lock.
        self._coordinator = MemoryStoreCoordinator(
            self._root,
            timeout=lock_timeout,
            lock_name=".curator-schedule.lock",
        )
        # This workspace-anchored lock fences the entire curator execution,
        # including MemoryManager instances configured with different data
        # roots.  The schedule lock alone only protects the small JSON ledger.
        self._execution_coordinator = MemoryStoreCoordinator(
            self._root,
            timeout=lock_timeout,
            lock_name=".curator-execution.lock",
        )
        # Serialize the short cross-store outbox handshake. The producer and
        # schedule files are still mutated in separate, non-nested lock
        # sections, while this workspace fence prevents a stale delivery from
        # resuming after its dedup receipt was settled and evicted.
        self._delivery_coordinator = MemoryStoreCoordinator(
            self._root,
            timeout=lock_timeout,
            lock_name=".curator-delivery.lock",
        )
        candidate_owner = owner_id or uuid.uuid4().hex
        if not isinstance(candidate_owner, str) or _HEX_32_RE.fullmatch(candidate_owner) is None:
            raise ValueError("owner_id must be 32 lowercase hexadecimal characters")
        self._owner_id = candidate_owner
        self._lease_seconds = _validate_lease_seconds(lease_seconds)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds

    @contextmanager
    def execution_fence(self) -> Iterator[None]:
        """Hold the workspace execution fence for claim, work, and ack."""

        self._ensure_safe_root()
        with self._execution_coordinator.transaction():
            yield

    @contextmanager
    def delivery_fence(self) -> Iterator[None]:
        """Serialize one producer-to-scheduler receipt handshake."""

        self._ensure_safe_root()
        with self._delivery_coordinator.transaction():
            yield

    def _require_delivery_fence(self) -> None:
        if not self._delivery_coordinator.in_transaction:
            raise CuratorScheduleError(
                "pinned completion mutation requires the delivery fence"
            )

    def _require_execution_fence(self) -> None:
        if not self._execution_coordinator.in_transaction:
            raise CuratorScheduleError(
                "curator claim mutation requires the workspace execution fence"
            )

    def snapshot(self) -> CuratorScheduleState:
        self._ensure_safe_root()
        with self._coordinator.transaction():
            return _validate_temporal_state(
                self._read_locked(),
                now_ms=_now_ms(None),
            )

    def record_completion(
        self,
        completion_id: str,
        *,
        pin: bool = False,
        promote_existing: bool = False,
    ) -> CuratorScheduleState:
        """Durably count one completion and optionally retain its dedup proof.

        ``pin=True`` is the scheduler half of the durable producer outbox
        protocol.  The producer must call :meth:`acknowledge_completion` only
        after its own delivery acknowledgement is durable.
        """

        self._validate_completion_id(completion_id)
        if not isinstance(pin, bool):
            raise TypeError("pin must be a boolean")
        if not isinstance(promote_existing, bool):
            raise TypeError("promote_existing must be a boolean")
        if promote_existing and not pin:
            raise ValueError("promote_existing requires pin=True")
        if pin:
            self._require_delivery_fence()
        current_ms = _now_ms(None)
        self._ensure_safe_root()
        # Unpinned producers also cross this fence so they cannot evict the
        # just-released receipt in the tiny scheduler-unpin -> producer-settle
        # interval of another delivery.
        with (
            self._delivery_coordinator.transaction(),
            self._coordinator.transaction(),
        ):
            state = _validate_temporal_state(
                self._read_locked(),
                now_ms=current_ms,
            )
            needs_upgrade = state.source_schema_version != _SCHEMA_VERSION
            state, replayed = self._record_completion_locked(
                state,
                completion_id,
                pin=pin,
                promote_existing=promote_existing,
            )
            state = replace(
                state,
                source_schema_version=_SCHEMA_VERSION,
            )
            if not replayed or needs_upgrade:
                self._write_locked(state)
            return state

    def acknowledge_completion(
        self,
        completion_id: str,
    ) -> CuratorScheduleState:
        """Release a producer pin while retaining normal recent dedup proof."""

        self._validate_completion_id(completion_id)
        self._require_delivery_fence()
        current_ms = _now_ms(None)
        self._ensure_safe_root()
        with self._coordinator.transaction():
            state = _validate_temporal_state(
                self._read_locked(),
                now_ms=current_ms,
            )
            needs_upgrade = state.source_schema_version != _SCHEMA_VERSION
            if completion_id in state.pinned_completion_ids:
                state = replace(
                    state,
                    generation=min(_MAX_COUNTER, state.generation + 1),
                    pinned_completion_ids=tuple(
                        receipt
                        for receipt in state.pinned_completion_ids
                        if receipt != completion_id
                    ),
                    source_schema_version=_SCHEMA_VERSION,
                )
                self._write_locked(state)
            elif needs_upgrade:
                state = replace(
                    state,
                    source_schema_version=_SCHEMA_VERSION,
                )
                self._write_locked(state)
            return state

    def claim_forced(
        self,
        *,
        now: float | None = None,
    ) -> CuratorClaimDecision:
        """Claim an administrative cycle without recording a task completion."""

        return self.claim_due(force=True, now=now)

    def claim_due(
        self,
        *,
        interval: int = 1,
        force: bool = False,
        now: float | None = None,
    ) -> CuratorClaimDecision:
        """Claim already-recorded debt without recording another completion."""

        self._require_execution_fence()
        threshold = 1 if force else _validate_interval(interval)
        current_ms = _now_ms(now)
        self._ensure_safe_root()
        with self._coordinator.transaction():
            prior_state = _validate_temporal_state(
                self._read_locked(),
                now_ms=current_ms,
            )
            needs_upgrade = (
                prior_state.source_schema_version != _SCHEMA_VERSION
            )
            state = self._drop_expired_claim(prior_state, now_ms=current_ms)
            state = replace(
                state,
                source_schema_version=_SCHEMA_VERSION,
            )
            if state.active_claim is not None:
                if needs_upgrade:
                    self._write_locked(state)
                return CuratorClaimDecision(state=state, reason="claim_active")
            if not force and current_ms < state.retry_not_before_ms:
                if state != prior_state or needs_upgrade:
                    self._write_locked(state)
                return CuratorClaimDecision(state=state, reason="retry_cooldown")
            if not force and state.pending_task_count < threshold:
                if state != prior_state or needs_upgrade:
                    self._write_locked(state)
                return CuratorClaimDecision(state=state)
            state, claim = self._install_claim(
                state,
                now_ms=current_ms,
                consume_task_count=(
                    state.pending_task_count if force else threshold
                ),
            )
            self._write_locked(state)
            return CuratorClaimDecision(
                state=state,
                claim=claim,
                reason="forced" if force else "claimed",
            )

    def finish_claim(
        self,
        claim_id: str,
        *,
        outcome: Literal["completed", "partial", "failed", "cancelled"],
        now: float | None = None,
    ) -> CuratorScheduleState:
        self._require_execution_fence()
        if not isinstance(claim_id, str) or _HEX_32_RE.fullmatch(claim_id) is None:
            raise ValueError("claim_id must be 32 lowercase hexadecimal characters")
        if outcome not in {"completed", "partial", "failed", "cancelled"}:
            raise ValueError("unsupported curator outcome")
        current_ms = _now_ms(now)
        self._ensure_safe_root()
        with self._coordinator.transaction():
            state = _validate_temporal_state(
                self._read_locked(),
                now_ms=current_ms,
            )
            claim = state.active_claim
            if (
                claim is None
                or claim.claim_id != claim_id
                or claim.owner_id != self._owner_id
            ):
                raise CuratorClaimLost(
                    "curator execution claim is no longer current"
                )
            updates: dict[str, Any] = {
                "generation": min(_MAX_COUNTER, state.generation + 1),
                "active_claim": None,
            }
            if outcome in {"completed", "partial"}:
                updates.update(
                    pending_task_count=max(
                        0,
                        state.pending_task_count - claim.consume_task_count,
                    ),
                    retry_not_before_ms=0,
                    consecutive_failures=0,
                    last_completed_at_ms=current_ms,
                    completed_cycle_count=min(
                        _MAX_COUNTER,
                        state.completed_cycle_count + 1,
                    ),
                )
            elif outcome == "failed":
                failures = min(_MAX_COUNTER, state.consecutive_failures + 1)
                exponent = min(10, max(0, failures - 1))
                delay = min(
                    _FAILURE_BACKOFF_MAX_SECONDS,
                    _FAILURE_BACKOFF_BASE_SECONDS * (2**exponent),
                )
                updates.update(
                    consecutive_failures=failures,
                    retry_not_before_ms=current_ms + int(delay * 1000),
                )
            else:
                updates["retry_not_before_ms"] = (
                    current_ms + int(_CANCELLATION_BACKOFF_SECONDS * 1000)
                )
            state = replace(state, **updates)
            state = replace(
                state,
                source_schema_version=_SCHEMA_VERSION,
            )
            self._write_locked(state)
            return state

    def renew_claim(
        self,
        claim_id: str,
        *,
        now: float | None = None,
        allow_expired: bool = False,
    ) -> CuratorClaim | None:
        """Renew a live matching claim, or return ``None`` once fenced out."""

        self._require_execution_fence()
        if not isinstance(claim_id, str) or _HEX_32_RE.fullmatch(claim_id) is None:
            raise ValueError("claim_id must be 32 lowercase hexadecimal characters")
        if not isinstance(allow_expired, bool):
            raise TypeError("allow_expired must be a boolean")
        current_ms = _now_ms(now)
        self._ensure_safe_root()
        with self._coordinator.transaction():
            state = _validate_temporal_state(
                self._read_locked(),
                now_ms=current_ms,
            )
            claim = state.active_claim
            if (
                claim is None
                or claim.claim_id != claim_id
                or claim.owner_id != self._owner_id
                or (
                    claim.lease_until_ms <= current_ms
                    and not allow_expired
                )
            ):
                return None
            renewed = replace(
                claim,
                claimed_at_ms=current_ms,
                lease_until_ms=(
                    current_ms + int(self._lease_seconds * 1000)
                ),
            )
            state = replace(
                state,
                generation=min(_MAX_COUNTER, state.generation + 1),
                active_claim=renewed,
                last_attempt_at_ms=current_ms,
                source_schema_version=_SCHEMA_VERSION,
            )
            self._write_locked(state)
            return renewed

    @staticmethod
    def _validate_completion_id(completion_id: object) -> None:
        if (
            not isinstance(completion_id, str)
            or _HEX_64_RE.fullmatch(completion_id) is None
        ):
            raise ValueError(
                "completion_id must be 64 lowercase hexadecimal characters"
            )

    @staticmethod
    def _record_completion_locked(
        state: CuratorScheduleState,
        completion_id: str,
        *,
        pin: bool,
        promote_existing: bool,
    ) -> tuple[CuratorScheduleState, bool]:
        if completion_id in state.recent_completion_ids:
            if (
                promote_existing
                and completion_id not in state.pinned_completion_ids
            ):
                # This path is authorized only after the producer was re-read
                # as unacknowledged under the workspace delivery fence. It
                # upgrades legacy receipts that predate scheduler pinning.
                return (
                    replace(
                        state,
                        generation=min(_MAX_COUNTER, state.generation + 1),
                        pinned_completion_ids=(
                            *state.pinned_completion_ids,
                            completion_id,
                        ),
                    ),
                    False,
                )
            return state, True
        if state.pending_task_count >= _MAX_COUNTER:
            raise CuratorScheduleCorrupt("pending task counter exhausted")
        receipts = [*state.recent_completion_ids]
        if len(receipts) >= _MAX_RECENT_COMPLETIONS:
            pinned = set(state.pinned_completion_ids)
            eviction_index = next(
                (
                    index
                    for index, receipt in enumerate(receipts)
                    if receipt not in pinned
                ),
                None,
            )
            if eviction_index is None:
                # Never accept a completion without retaining its immediate
                # replay proof. Backpressure is safer than double-counting.
                raise CuratorScheduleError(
                    "completion deduplication capacity is pinned"
                )
            receipts.pop(eviction_index)
        receipts.append(completion_id)
        pinned_receipts = state.pinned_completion_ids
        if pin:
            pinned_receipts = (*pinned_receipts, completion_id)
        return (
            replace(
                state,
                generation=min(_MAX_COUNTER, state.generation + 1),
                pending_task_count=state.pending_task_count + 1,
                recent_completion_ids=tuple(receipts),
                pinned_completion_ids=pinned_receipts,
            ),
            False,
        )

    def _drop_expired_claim(
        self,
        state: CuratorScheduleState,
        *,
        now_ms: int,
    ) -> CuratorScheduleState:
        claim = state.active_claim
        if claim is None or claim.lease_until_ms > now_ms:
            return state
        return replace(
            state,
            generation=min(_MAX_COUNTER, state.generation + 1),
            active_claim=None,
            consecutive_failures=min(
                _MAX_COUNTER,
                state.consecutive_failures + 1,
            ),
        )

    def _install_claim(
        self,
        state: CuratorScheduleState,
        *,
        now_ms: int,
        consume_task_count: int,
    ) -> tuple[CuratorScheduleState, CuratorClaim]:
        lease_ms = int(self._lease_seconds * 1000)
        claim = CuratorClaim(
            claim_id=uuid.uuid4().hex,
            owner_id=self._owner_id,
            claimed_at_ms=now_ms,
            lease_until_ms=now_ms + lease_ms,
            consume_task_count=consume_task_count,
        )
        return (
            replace(
                state,
                generation=min(_MAX_COUNTER, state.generation + 1),
                active_claim=claim,
                last_attempt_at_ms=now_ms,
            ),
            claim,
        )

    def _read_locked(self) -> CuratorScheduleState:
        descriptor: int | None = None
        try:
            path_info = os.lstat(self._path)
            if (
                not stat.S_ISREG(path_info.st_mode)
                or path_info.st_size > MAX_CURATOR_SCHEDULE_BYTES
            ):
                raise CuratorScheduleCorrupt("unsafe schedule authority")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(self._path, flags)
            descriptor_info = os.fstat(descriptor)
            current_info = os.lstat(self._path)
            if (
                not stat.S_ISREG(descriptor_info.st_mode)
                or descriptor_info.st_size > MAX_CURATOR_SCHEDULE_BYTES
                or descriptor_info.st_dev != current_info.st_dev
                or descriptor_info.st_ino != current_info.st_ino
                or path_info.st_dev != current_info.st_dev
                or path_info.st_ino != current_info.st_ino
            ):
                raise CuratorScheduleCorrupt("unstable schedule authority")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(16_384, MAX_CURATOR_SCHEDULE_BYTES + 1 - total),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_CURATOR_SCHEDULE_BYTES:
                    raise CuratorScheduleCorrupt("oversized schedule authority")
            try:
                payload = json.loads(b"".join(chunks).decode("utf-8"))
            except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as error:
                raise CuratorScheduleCorrupt("malformed schedule authority") from error
            return _decode_state(payload)
        except FileNotFoundError:
            return CuratorScheduleState()
        except OSError as error:
            raise CuratorScheduleCorrupt("schedule authority unavailable") from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _ensure_safe_root(self) -> None:
        """Reject redirected roots before the lock backend can create files."""

        workspace = self._root.parent
        try:
            workspace_info = os.lstat(workspace)
        except OSError as error:
            raise CuratorScheduleCorrupt("workspace is unavailable") from error
        if not stat.S_ISDIR(workspace_info.st_mode):
            raise CuratorScheduleCorrupt("workspace is unsafe")
        try:
            os.mkdir(self._root, mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise CuratorScheduleCorrupt("schedule root is unavailable") from error
        try:
            root_info = os.lstat(self._root)
        except OSError as error:
            raise CuratorScheduleCorrupt("schedule root is unavailable") from error
        if not stat.S_ISDIR(root_info.st_mode):
            raise CuratorScheduleCorrupt("schedule root is unsafe")

    def _write_locked(self, state: CuratorScheduleState) -> None:
        payload = _encode_state(state)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_CURATOR_SCHEDULE_BYTES:
            raise CuratorScheduleCorrupt("schedule authority exceeds size limit")
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_info = os.lstat(self._root)
        if not stat.S_ISDIR(root_info.st_mode):
            raise CuratorScheduleCorrupt("unsafe schedule root")
        try:
            target_info = os.lstat(self._path)
        except FileNotFoundError:
            target_info = None
        if target_info is not None and not stat.S_ISREG(target_info.st_mode):
            raise CuratorScheduleCorrupt("unsafe schedule target")
        descriptor, temporary = tempfile.mkstemp(
            dir=self._root,
            prefix=".curator-schedule-",
            suffix=".tmp",
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            directory_descriptor = os.open(
                self._root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


__all__ = [
    "MAX_CURATOR_INTERVAL_TASKS",
    "MAX_CURATOR_SCHEDULE_BYTES",
    "CuratorClaim",
    "CuratorClaimDecision",
    "CuratorClaimLost",
    "CuratorScheduleCorrupt",
    "CuratorScheduleError",
    "CuratorScheduleState",
    "CuratorScheduleStore",
    "curator_completion_id",
    "validate_curator_schedule_payload",
]
