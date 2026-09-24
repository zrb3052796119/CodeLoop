"""Pure, versioned interpretation of persisted Memory approval state.

The persistent Manager and the read-only approval authority consume the same
entry format but deliberately use different stale-hash policies.  This module
owns that state machine without importing ``MemoryEntry`` or performing I/O,
time reads, audit writes, or in-place mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

APPROVAL_INTERPRETATION_VERSION = 4

_ACTIVE = "active"
_APPROVED = "approved"
_PENDING = "pending"
_REJECTED = "rejected"
_SAFE = "safe"
_SUSPICIOUS = "suspicious"
_UNSAFE = "unsafe"
_VALID_APPROVAL_STATUSES = frozenset({_APPROVED, _PENDING, _REJECTED})
_USER_REVIEW_REQUIRED = "user_review_required"
_VALID_APPROVAL_POLICIES = frozenset(
    {"user_explicit", _USER_REVIEW_REQUIRED, "auto_approve_verified"}
)


class ApprovalInterpretationMode(str, Enum):
    """Named invalid-authority behavior for one persisted-entry consumer."""

    STRICT_FAIL_CLOSED = "strict_fail_closed"
    RECOVER_TO_PENDING = "recover_to_pending"


class ApprovalInterpretationFailure(str, Enum):
    """Typed failures returned by the pure interpreter."""

    INVALID_APPROVAL_POLICY = "invalid_approval_policy"
    INCOMPLETE_APPROVAL_AUTHORITY = "incomplete_approval_authority"
    STALE_APPROVAL_HASH = "stale_approval_hash"


@dataclass(frozen=True, slots=True)
class ApprovalAuthorityState:
    """Normalized authority fields before or after load interpretation."""

    approval_status: str
    approval_content_hash: str
    approval_reason: str
    approval_actor: str
    approval_decided_at: float
    approval_policy: str
    safety_status: str
    safety_reason: str
    lifecycle_status: str
    tier: str
    tier_reason: str
    curator_locked: bool


@dataclass(frozen=True, slots=True)
class ApprovalInterpretationInput:
    """Complete deterministic evidence needed to interpret one loaded entry."""

    state: ApprovalAuthorityState
    raw_has_approval: bool
    raw_has_hash: bool
    assessed_safety_status: str
    assessed_safety_reason: str
    canonical_hash: str
    transition_time: float
    raw_has_policy: bool = False
    raw_approval_policy: str | None = None
    requires_complete_authority: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalInterpretation:
    """One interpreted state or a typed fail-closed disposition."""

    state: ApprovalAuthorityState | None
    failure: ApprovalInterpretationFailure | None = None
    migration_required: bool = False


def interpret_loaded_approval(
    request: ApprovalInterpretationInput,
    *,
    mode: ApprovalInterpretationMode,
) -> ApprovalInterpretation:
    """Interpret persisted approval fields without side effects.

    Input ``state`` is already normalized by the persistence decoder.  Safety
    and the canonical approval hash are freshly computed from that normalized
    entry and therefore replace their persisted projections.

    ``migration_required`` deliberately models the Manager's historical audit
    and persistence trigger.  It is always false for a strict read-only view.
    """

    if not isinstance(mode, ApprovalInterpretationMode):
        raise TypeError("mode must be an ApprovalInterpretationMode")

    original = request.state
    invalid_policy = bool(
        request.raw_has_policy
        and request.raw_approval_policy not in _VALID_APPROVAL_POLICIES
    )
    if (
        mode is ApprovalInterpretationMode.STRICT_FAIL_CLOSED
        and invalid_policy
    ):
        return ApprovalInterpretation(
            state=None,
            failure=ApprovalInterpretationFailure.INVALID_APPROVAL_POLICY,
        )
    stale_hash = bool(
        request.raw_has_hash
        and original.approval_content_hash
        and original.approval_content_hash != request.canonical_hash
    )
    incomplete_authority = bool(
        request.requires_complete_authority
        and (
            not request.raw_has_approval
            or not request.raw_has_hash
            or not original.approval_content_hash
        )
    )
    if (
        mode is ApprovalInterpretationMode.STRICT_FAIL_CLOSED
        and incomplete_authority
    ):
        return ApprovalInterpretation(
            state=None,
            failure=(
                ApprovalInterpretationFailure.INCOMPLETE_APPROVAL_AUTHORITY
            ),
        )
    if mode is ApprovalInterpretationMode.STRICT_FAIL_CLOSED and stale_hash:
        return ApprovalInterpretation(
            state=None,
            failure=ApprovalInterpretationFailure.STALE_APPROVAL_HASH,
        )

    recover = mode is ApprovalInterpretationMode.RECOVER_TO_PENDING
    state = replace(
        original,
        safety_status=request.assessed_safety_status,
        safety_reason=request.assessed_safety_reason,
    )
    migration_required = False

    if invalid_policy:
        state = replace(
            state,
            approval_status=_PENDING,
            approval_policy=_USER_REVIEW_REQUIRED,
            approval_reason="invalid approval policy after load",
            approval_actor="migration",
            approval_decided_at=request.transition_time,
        )
        migration_required = recover

    if invalid_policy:
        # The explicit persisted policy has no legacy interpretation.  Keep
        # the entry review-gated until a new authority decision is made.  The
        # unsafe-content gate remains the one stricter exception.
        if request.assessed_safety_status == _UNSAFE:
            state = replace(
                state,
                approval_status=_REJECTED,
                approval_reason=request.assessed_safety_reason,
                approval_actor="safety_gate",
            )
    elif incomplete_authority:
        if request.assessed_safety_status == _UNSAFE:
            approval_status = _REJECTED
            approval_reason = request.assessed_safety_reason
            approval_actor = "safety_gate"
        else:
            approval_status = _PENDING
            approval_reason = "incomplete approval authority after load"
            approval_actor = "migration"
        state = replace(
            state,
            approval_status=approval_status,
            approval_reason=approval_reason,
            approval_actor=approval_actor,
            approval_decided_at=request.transition_time,
        )
        migration_required = recover
    elif not request.raw_has_approval:
        if request.assessed_safety_status == _UNSAFE:
            approval_status = _REJECTED
        elif (
            request.assessed_safety_status == _SUSPICIOUS
            or state.approval_policy == _USER_REVIEW_REQUIRED
        ):
            approval_status = _PENDING
        elif request.assessed_safety_status == _SAFE:
            approval_status = (
                _APPROVED if state.lifecycle_status == _ACTIVE else _PENDING
            )
        else:
            approval_status = _PENDING
        state = replace(
            state,
            approval_status=approval_status,
            approval_reason=request.assessed_safety_reason or "legacy migration",
            approval_actor="migration",
            approval_decided_at=(
                request.transition_time if recover else state.approval_decided_at
            ),
        )
        migration_required = recover
    elif request.assessed_safety_status == _UNSAFE:
        state = replace(
            state,
            approval_status=_REJECTED,
            approval_reason=request.assessed_safety_reason,
            approval_actor="safety_gate",
            approval_decided_at=(
                request.transition_time if recover else state.approval_decided_at
            ),
        )
        migration_required = recover
    elif stale_hash:
        # STRICT returned above.  Recovery invalidates the old decision while
        # retaining the entry for a fresh review.
        state = replace(
            state,
            approval_status=_PENDING,
            approval_reason="approval hash mismatch after load",
            approval_actor="migration",
            approval_decided_at=request.transition_time,
        )
        migration_required = recover
    elif not request.raw_has_hash:
        if (
            request.assessed_safety_status == _SUSPICIOUS
            and state.approval_status != _REJECTED
        ):
            state = replace(
                state,
                approval_status=_PENDING,
                approval_reason=request.assessed_safety_reason,
                approval_actor="migration",
            )
        migration_required = recover

    if state.approval_status == _REJECTED:
        state = replace(
            state,
            lifecycle_status=_REJECTED,
            curator_locked=False,
        )
    if state.safety_status == _UNSAFE:
        state = replace(
            state,
            lifecycle_status=_REJECTED,
            tier_reason="safety_gate",
        )

    if state.approval_content_hash != request.canonical_hash:
        state = replace(state, approval_content_hash=request.canonical_hash)
        migration_required = migration_required or recover
    if not state.approval_actor:
        state = replace(state, approval_actor="migration")
        migration_required = migration_required or recover
    if state.approval_status not in _VALID_APPROVAL_STATUSES:
        state = replace(state, approval_status=_PENDING)
        migration_required = migration_required or recover

    return ApprovalInterpretation(
        state=state,
        migration_required=migration_required,
    )


__all__ = [
    "APPROVAL_INTERPRETATION_VERSION",
    "ApprovalAuthorityState",
    "ApprovalInterpretation",
    "ApprovalInterpretationFailure",
    "ApprovalInterpretationInput",
    "ApprovalInterpretationMode",
    "interpret_loaded_approval",
]
