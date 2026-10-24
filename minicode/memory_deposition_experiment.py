"""Permissive deposition experiment — capture value-gate-rejected lessons.

Background (2026-10-21 review): the write path drops every claim the
``ReflectionValueGate`` rejects, even when the claim validator accepted its
groundedness (single-observation error patterns, environment-scoped errors,
claims without a durable signal). The 2026-08/09 pollution studies measured
that injected noise costs token overhead, not task failures, while a dropped
lesson is unrecoverable — so this experiment re-routes those claims into
draft entries instead of discarding them.

Modes (env ``MINI_CODE_MEMORY_DEPOSITION_MODE``):

- ``strict``   (default) historical behavior; value-gate rejections dropped.
- ``capture``  rejected-but-validated claims are written as pending drafts.
               They queue for review and never become injectable on their
               own (``is_active`` stays false for pending approval).
- ``inject``   drafts are additionally auto-approved and retrievable until
               their TTL expires; the canonical retriever skips expired
               drafts, so no background sweeper is required.

TTL (env ``MINI_CODE_MEMORY_DEPOSITION_DRAFT_TTL_DAYS``, default 14) only
applies to ``inject`` drafts.

When ``MINI_CODE_MEMORY_REDIS_URL`` is configured, new inject-mode drafts
prefer the disposable hot store and a separate, budgeted prompt lane. They
never enter the canonical corpus. Redis failures retain the file path above;
``MINI_CODE_MEMORY_REDIS_DRAFT_TTL`` overrides only the hot draft TTL in seconds.

Rollback: set ``MINI_CODE_MEMORY_DEPOSITION_MODE=strict`` or unset it. The
next write behaves exactly as before the experiment; drafts already on disk
stay stored but are inert (capture drafts were never injectable, inject
drafts age out through the retriever filter). Delete entries carrying
``metadata.deposition_draft`` manually if a clean ledger is wanted.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

DEPOSITION_MODE_ENV = "MINI_CODE_MEMORY_DEPOSITION_MODE"
DRAFT_TTL_ENV = "MINI_CODE_MEMORY_DEPOSITION_DRAFT_TTL_DAYS"
DEFAULT_DRAFT_TTL_DAYS = 14
PENDING_DRAFT_DAYS_ENV = "MINI_CODE_MEMORY_PENDING_DRAFT_DAYS"
DEFAULT_PENDING_DRAFT_DAYS = 7
_FEATURE_ENVS = {
    "user_preferences": "MINI_CODE_MEMORY_DEPOSITION_USER_PREFERENCES",
    "declared_dependencies": "MINI_CODE_MEMORY_DEPOSITION_DECLARED_DEPENDENCIES",
    "pending_drafts": "MINI_CODE_MEMORY_DEPOSITION_PENDING_DRAFTS",
}

_VALID_MODES = ("strict", "capture", "inject")


def deposition_mode() -> str:
    """Resolve the experiment mode; unknown values fail closed to strict."""
    raw = str(os.environ.get(DEPOSITION_MODE_ENV, "")).strip().lower()
    return raw if raw in _VALID_MODES else "strict"


def deposition_feature_enabled(feature: str) -> bool:
    """Enable each lane in experimental modes; explicit invalid flags fail closed."""
    env = _FEATURE_ENVS[feature]
    return deposition_mode() != "strict" and os.environ.get(env, "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def pending_draft_age_seconds() -> int:
    """Age threshold for read-side pending drafts, defaulting to seven days."""
    try:
        days = int(os.environ.get(PENDING_DRAFT_DAYS_ENV, ""))
    except ValueError:
        days = DEFAULT_PENDING_DRAFT_DAYS
    return (days if days > 0 else DEFAULT_PENDING_DRAFT_DAYS) * 86400


def draft_ttl_seconds() -> int:
    """Resolve the inject-mode draft TTL in seconds (default 14 days)."""
    raw = os.environ.get(DRAFT_TTL_ENV, "")
    try:
        days = int(raw)
    except ValueError:
        days = DEFAULT_DRAFT_TTL_DAYS
    if days <= 0:
        days = DEFAULT_DRAFT_TTL_DAYS
    return days * 86400


def draft_operation_id(primary_operation_id: str) -> str:
    """Derive a deterministic, regex-valid operation id for the draft batch."""
    digest = hashlib.sha256(
        f"deposition-draft:{primary_operation_id}".encode()
    ).hexdigest()[:32]
    return f"memoryop_{digest}"


def apply_draft_metadata(
    add_entry_kwargs: dict[str, Any],
    *,
    reason_codes: list[str],
    expires_at: float | None,
    injectable: bool,
) -> dict[str, Any]:
    """Return a copy of ``add_entry_kwargs`` marked as a deposition draft.

    ``injectable=True`` flips the approval policy to auto-approve so the
    draft passes ``is_active`` and reaches retrieval; ``injectable=False``
    keeps the builder's ``USER_REVIEW_REQUIRED`` policy (pending queue).
    """
    kwargs = dict(add_entry_kwargs)
    if injectable:
        from minicode.memory import MemoryApprovalPolicy

        kwargs["approval_policy"] = MemoryApprovalPolicy.AUTO_APPROVE_VERIFIED
    metadata = dict(kwargs.get("metadata") or {})
    metadata["deposition_draft"] = {
        "reason_codes": [str(code) for code in reason_codes][:8],
        "expires_at": expires_at,
        "injectable": bool(injectable),
    }
    kwargs["metadata"] = metadata
    return kwargs


def draft_entry_retrievable(entry: Any) -> bool:
    """Report whether ``entry`` may enter the canonical retrieval corpus.

    Non-draft entries are always eligible.  Inject-mode drafts age out past
    their ``expires_at``; capture-mode drafts never pass ``is_active`` in
    the first place, so the check is a cheap no-op for them.
    """
    metadata = getattr(entry, "metadata", None)
    if not isinstance(metadata, dict):
        return True
    draft = metadata.get("deposition_draft")
    if not isinstance(draft, dict):
        return True
    reasons = draft.get("reason_codes", [])
    for reason, feature in (("user_preference", "user_preferences"), ("declared_dependency", "declared_dependencies")):
        if reason in reasons and (deposition_mode() != "inject" or not deposition_feature_enabled(feature)):
            return False
    expires_at = draft.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return True
    return time.time() < float(expires_at)
