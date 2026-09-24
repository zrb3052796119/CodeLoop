"""Trusted, bounded user evidence admitted by top-level entrypoints only.

Ordinary chat messages are not reflection authority: nested agents and the
agent loop itself also use ``role=user`` messages.  This module therefore
keeps durable user evidence in a small typed carrier which only trusted
top-level entrypoints construct and the agent loop merely transports.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

USER_EVIDENCE_SCHEMA_VERSION = 1
MAX_USER_EVIDENCE_SEEDS = 8
MAX_USER_EVIDENCE_STATEMENT_CHARS = 500
_MAX_PROMPT_SCAN_CHARS = 4_000

UserEvidenceKind = Literal["stable_constraint", "followup_correction"]
UserEvidenceOrigin = Literal[
    "direct_user_request",
    "explicit_correction_followup",
]

_TURN_ID_RE = re.compile(r"turn_[0-9a-f]{32}")
_RUN_ID_RE = re.compile(r"run_[0-9a-f]{32}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_CONSTRAINT_CUE_RE = re.compile(
    r"\b(?:must|must not|do not|don't|never|required|only)\b|"
    r"必须|不要|不得|不能|只能|仅限",
    re.IGNORECASE,
)
_STABLE_SCOPE_RE = re.compile(
    r"\b(?:this\s+)?(?:project|repository|repo|codebase)"
    r"(?:[-\s](?:wide|level))?\b|"
    r"\bpublic\s+(?:api|interface|contract)\b|"
    r"\bbackwards?[-\s]?compatib(?:le|ility)\b|"
    r"\b(?:from now on|going forward|always|all future tasks?|"
    r"project convention|repository convention|team convention)\b|"
    r"(?:本项目|项目中|项目内|项目级|项目长期|整个项目|仓库中|仓库内|仓库级|"
    r"代码库|公共接口|公开接口|公共\s*API|公开\s*API|向后兼容|"
    r"从现在起|从今以后|今后|以后都|始终|一律|所有后续任务)",
    re.IGNORECASE,
)
_TRANSIENT_SCOPE_RE = re.compile(
    r"\b(?:this|current|next)\s+(?:turn|task|run|request|session)\b|"
    r"\b(?:for now|temporarily|today|this time|once)\b|"
    r"(?:本轮|这一轮|本次|这次|当前任务|当前请求|当前会话|暂时|今天|这一次)",
    re.IGNORECASE,
)
_EXPLICIT_CORRECTION_RE = re.compile(
    r"^(?:correction|correcting|to\s+correct|let\s+me\s+correct)"
    r"(?:\s+that)?\s*[:;,\-]?\s+|"
    r"\b(?:that|this|it|your\s+(?:answer|change|implementation)|"
    r"the\s+(?:previous|last)\s+(?:answer|change|implementation))\b"
    r"[^.!?]{0,160}\b(?:is|was|were)\s+(?:wrong|incorrect)\b|"
    r"\byou\s+(?:got|understood|implemented)\b[^.!?]{0,120}\bwrong\b|"
    r"^(?:纠正|更正)\s*[:：,，;；]?\s*|"
    r"^(?:不对|错了)\s*[:：,，;；]\s*|"
    r"(?:这|那|它|你的|上一次|上一条|之前的)[^。！？]{0,120}"
    r"(?:不对|错了|是错误的)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"^(?:what|why|how|when|where|who|can|could|would|do|does|did|"
    r"is|are|was|were|should\s+(?:i|we|you))\b|"
    r"^(?:什么|为什么|怎么|如何|是否|能否|可以|要不要|是不是)",
    re.IGNORECASE,
)


def _statement_digest(statement: str) -> str:
    payload = (
        "minicode.trusted-user-evidence.statement.v1\0" + statement
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def committed_user_evidence_operation_id(
    current_turn_id: str,
    current_run_id: str | None,
) -> str:
    """Derive the retry identity for one Session-committed evidence batch."""
    if _TURN_ID_RE.fullmatch(current_turn_id) is None or not _valid_id(
        current_run_id, _RUN_ID_RE
    ):
        raise ValueError("invalid committed user evidence identity")
    encoded = json.dumps(
        {
            "schema": "committed_user_evidence_memory_v1",
            "turn_id": current_turn_id,
            "run_id": current_run_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"memoryop_{uuid.uuid5(uuid.NAMESPACE_OID, encoded).hex}"


def _valid_id(value: str | None, pattern: re.Pattern[str]) -> bool:
    return value is None or (
        isinstance(value, str) and pattern.fullmatch(value) is not None
    )


def _is_durable_project_constraint(statement: str) -> bool:
    return bool(
        isinstance(statement, str)
        and statement == statement.strip()
        and 0 < len(statement) <= MAX_USER_EVIDENCE_STATEMENT_CHARS
        and "\x00" not in statement
        and _CONSTRAINT_CUE_RE.search(statement)
        and _STABLE_SCOPE_RE.search(statement)
        and not _TRANSIENT_SCOPE_RE.search(statement)
    )


@dataclass(frozen=True, slots=True)
class TrustedUserEvidenceSeed:
    """One immutable user-authored fact with explicit entrypoint provenance."""

    kind: UserEvidenceKind
    statement: str
    origin: UserEvidenceOrigin
    statement_digest: str
    current_turn_id: str | None = None
    current_run_id: str | None = None
    prior_turn_id: str | None = None
    prior_run_id: str | None = None
    schema_version: int = USER_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != USER_EVIDENCE_SCHEMA_VERSION
            or self.kind not in {"stable_constraint", "followup_correction"}
            or self.origin
            not in {"direct_user_request", "explicit_correction_followup"}
            or not isinstance(self.statement, str)
            or self.statement != self.statement.strip()
            or not self.statement
            or len(self.statement) > MAX_USER_EVIDENCE_STATEMENT_CHARS
            or "\x00" in self.statement
            or not isinstance(self.statement_digest, str)
            or _DIGEST_RE.fullmatch(self.statement_digest) is None
            or self.statement_digest != _statement_digest(self.statement)
            or not _valid_id(self.current_turn_id, _TURN_ID_RE)
            or not _valid_id(self.current_run_id, _RUN_ID_RE)
            or not _valid_id(self.prior_turn_id, _TURN_ID_RE)
            or not _valid_id(self.prior_run_id, _RUN_ID_RE)
        ):
            raise ValueError("invalid trusted user evidence seed")
        if self.kind == "stable_constraint":
            if (
                self.origin != "direct_user_request"
                or not _is_durable_project_constraint(self.statement)
                or self.prior_turn_id is not None
                or self.prior_run_id is not None
                or (self.current_turn_id is None)
                != (self.current_run_id is None)
            ):
                raise ValueError("invalid stable constraint provenance")
            return
        if (
            self.origin != "explicit_correction_followup"
            or not looks_like_correction(self.statement)
            or self.current_turn_id is None
            or self.current_run_id is None
            or self.prior_turn_id is None
            or self.prior_run_id is None
            or self.current_turn_id == self.prior_turn_id
            or self.current_run_id == self.prior_run_id
        ):
            raise ValueError("invalid correction provenance")

    @classmethod
    def stable_constraint(
        cls,
        statement: str,
        *,
        current_turn_id: str | None = None,
        current_run_id: str | None = None,
    ) -> TrustedUserEvidenceSeed:
        normalized = statement.strip()
        return cls(
            kind="stable_constraint",
            statement=normalized,
            origin="direct_user_request",
            statement_digest=_statement_digest(normalized),
            current_turn_id=current_turn_id,
            current_run_id=current_run_id,
        )

    @classmethod
    def followup_correction(
        cls,
        statement: str,
        *,
        current_turn_id: str,
        current_run_id: str,
        prior_turn_id: str,
        prior_run_id: str,
    ) -> TrustedUserEvidenceSeed:
        normalized = statement.strip()
        if not looks_like_correction(normalized):
            raise ValueError("follow-up message is not an explicit correction")
        return cls(
            kind="followup_correction",
            statement=normalized,
            origin="explicit_correction_followup",
            statement_digest=_statement_digest(normalized),
            current_turn_id=current_turn_id,
            current_run_id=current_run_id,
            prior_turn_id=prior_turn_id,
            prior_run_id=prior_run_id,
        )

    def to_trace_event(self) -> dict[str, object]:
        event: dict[str, object] = {
            "type": (
                "user_constraint"
                if self.kind == "stable_constraint"
                else "user_correction"
            ),
            "content": self.statement,
            "source": "trusted_user_entrypoint",
            "origin": self.origin,
            "statement_digest": self.statement_digest,
            "user_evidence_schema_version": self.schema_version,
        }
        for key, value in (
            ("current_turn_id", self.current_turn_id),
            ("current_run_id", self.current_run_id),
            ("prior_turn_id", self.prior_turn_id),
            ("prior_run_id", self.prior_run_id),
        ):
            if value is not None:
                event[key] = value
        return event


def _bounded_sentences(prompt: str) -> tuple[str, ...]:
    if not isinstance(prompt, str):
        return ()
    bounded = prompt[:_MAX_PROMPT_SCAN_CHARS]
    if len(prompt) > len(bounded):
        last_boundary = max(
            (bounded.rfind(marker) for marker in ("\n", ".", "!", "?", "。", "！", "？")),
            default=-1,
        )
        if last_boundary < 0:
            return ()
        bounded = bounded[: last_boundary + 1]
    return tuple(
        item.strip()
        for item in re.split(r"(?<=[.!?。！？])\s*|\n+", bounded)
        if item.strip()
    )


def durable_project_constraints(prompt: str) -> tuple[str, ...]:
    """Select only explicit, non-transient project-level constraints."""
    selected: list[str] = []
    for statement in _bounded_sentences(prompt):
        if (
            not _is_durable_project_constraint(statement)
            or statement in selected
        ):
            continue
        selected.append(statement)
        if len(selected) >= MAX_USER_EVIDENCE_SEEDS:
            break
    return tuple(selected)


def stable_constraint_seeds(prompt: str) -> tuple[TrustedUserEvidenceSeed, ...]:
    return tuple(
        TrustedUserEvidenceSeed.stable_constraint(statement)
        for statement in durable_project_constraints(prompt)
    )


def looks_like_correction(statement: str) -> bool:
    if (
        not isinstance(statement, str)
        or statement != statement.strip()
        or not 0 < len(statement) <= MAX_USER_EVIDENCE_STATEMENT_CHARS
        or "\x00" in statement
        or statement.endswith(("?", "？"))
        or _QUESTION_RE.search(statement)
    ):
        return False
    return bool(_EXPLICIT_CORRECTION_RE.search(statement))


def normalize_trusted_user_evidence(
    value: object,
    *,
    agent_depth: int,
) -> tuple[TrustedUserEvidenceSeed, ...]:
    """Validate a whole seed batch, failing closed rather than partially."""
    if agent_depth != 0 or not isinstance(value, tuple):
        return ()
    if len(value) > MAX_USER_EVIDENCE_SEEDS:
        return ()
    if any(type(item) is not TrustedUserEvidenceSeed for item in value):
        return ()
    rebuilt: list[TrustedUserEvidenceSeed] = []
    try:
        for item in value:
            rebuilt.append(
                TrustedUserEvidenceSeed(
                    kind=item.kind,
                    statement=item.statement,
                    origin=item.origin,
                    statement_digest=item.statement_digest,
                    current_turn_id=item.current_turn_id,
                    current_run_id=item.current_run_id,
                    prior_turn_id=item.prior_turn_id,
                    prior_run_id=item.prior_run_id,
                    schema_version=item.schema_version,
                )
            )
    except (AttributeError, TypeError, ValueError):
        return ()
    keys = [
        (item.kind, item.statement_digest)
        for item in rebuilt
    ]
    if len(set(keys)) != len(keys):
        return ()
    current_turn_ids = {
        item.current_turn_id for item in rebuilt if item.current_turn_id is not None
    }
    current_run_ids = {
        item.current_run_id for item in rebuilt if item.current_run_id is not None
    }
    if len(current_turn_ids) > 1 or len(current_run_ids) > 1:
        return ()
    return tuple(rebuilt)


def bind_trusted_user_evidence_to_prompt(
    value: object,
    prompt: object,
    *,
    agent_depth: int,
    current_turn_id: str | None = None,
    current_run_id: str | None = None,
    bind_missing_current: bool = False,
) -> tuple[TrustedUserEvidenceSeed, ...]:
    """Prove that every trusted seed is authored by the current user prompt.

    The typed carrier is provenance, not a capability for inventing text.  A
    correction must equal the complete direct follow-up, while a stable
    constraint must be one of the exact bounded sentences selected from that
    prompt.  A mixed or mismatched batch is rejected as a whole.
    """
    normalized = normalize_trusted_user_evidence(value, agent_depth=agent_depth)
    if not normalized or not isinstance(prompt, str):
        return ()
    stripped_prompt = prompt.strip()
    stable_statements = set(durable_project_constraints(prompt))
    bound: list[TrustedUserEvidenceSeed] = []
    for seed in normalized:
        if seed.kind == "followup_correction":
            if (
                seed.statement != stripped_prompt
                or seed.current_turn_id != current_turn_id
                or seed.current_run_id != current_run_id
            ):
                return ()
            bound.append(seed)
            continue

        if seed.statement not in stable_statements:
            return ()
        if seed.current_turn_id is not None and (
            seed.current_turn_id != current_turn_id
            or seed.current_run_id != current_run_id
        ):
            return ()
        if (
            bind_missing_current
            and seed.current_turn_id is None
            and current_turn_id is not None
            and current_run_id is not None
        ):
            seed = TrustedUserEvidenceSeed.stable_constraint(
                seed.statement,
                current_turn_id=current_turn_id,
                current_run_id=current_run_id,
            )
        bound.append(seed)
    return tuple(bound)


__all__ = [
    "MAX_USER_EVIDENCE_SEEDS",
    "MAX_USER_EVIDENCE_STATEMENT_CHARS",
    "USER_EVIDENCE_SCHEMA_VERSION",
    "TrustedUserEvidenceSeed",
    "bind_trusted_user_evidence_to_prompt",
    "committed_user_evidence_operation_id",
    "durable_project_constraints",
    "looks_like_correction",
    "normalize_trusted_user_evidence",
    "stable_constraint_seeds",
]
