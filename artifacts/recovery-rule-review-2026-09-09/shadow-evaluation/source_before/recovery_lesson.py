"""Per-episode recovery lesson generation.

Implements step 2-3 of the 2026-09-09 recovery-rule review: bounded evidence
episodes keep the invocation (command, args, cwd, timeout) and the diff
excerpt that the whole-task projection used to drop, and a dedicated
no-tools agent -- the existing ``LLMReflectionSynthesizer`` -- judges each
episode independently. Every produced claim still passes the same shared
validator and value gate as rule output; episodes whose relationship the
trace cannot establish (for example a timeout-budget retry after a
transient failure) are surfaced as observations, never promoted to
confirmed lessons.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from minicode.reflection_evidence import (
    ErrorEvidence,
    RecoveryEvidence,
    TaskEvidence,
    ToolEvidence,
    VerificationEvidence,
    call_objective,
    call_objective_from_rendered,
    objectives_same_target,
    _is_transient_environmental_failure,
)
from minicode.reflection_llm import (
    LLMReflectionSynthesizer,
    ReflectionLLMConfig,
    StructuredGenerationClient,
)
from minicode.reflection_synthesis import (
    ClaimValidationResult,
    ReflectionCandidate,
    ReflectionClaim,
    ReflectionClaimValidator,
    ReflectionValueGate,
    ReflectionValueDecision,
    _passed_verifications_after_recovery,
    _verification_evidence_matches_recovery,
)

EPISODE_MAX_ITEMS = 10
_EPISODE_MAX_CALLS = 4
_EPISODE_MAX_FOLLOWUPS = 2
_EPISODE_MAX_FAILED_CHECKS = 4


@dataclass(frozen=True)
class RecoveryEpisode:
    """One bounded failure-and-recovery segment of the task trace."""

    episode_id: str
    related_error_ids: tuple[str, ...]
    # Failed calls with their bounded invocation rendering (command, args,
    # cwd, timeout included) -- the input the whole-task envelope dropped.
    failed_calls: tuple[ToolEvidence, ...]
    # Later calls on the same tool whose invocation materially changed.
    followup_calls: tuple[ToolEvidence, ...]
    recovery: RecoveryEvidence | None
    verifications: tuple[VerificationEvidence, ...]
    event_ids: tuple[str, ...]
    # ``verified``: the trace establishes the recovery->verification link.
    # ``candidate``: inputs changed and the target later succeeded, but the
    # causal relationship is not established (e.g. transient failure, or no
    # linked passing check). Candidate episodes may only yield inferred
    # observations, never confirmed lessons.
    causal_status: Literal["verified", "candidate"] = "candidate"


@dataclass
class EpisodeLessonOutcome:
    """What one episode produced after the shared admission gates."""

    episode_id: str
    causal_status: Literal["verified", "candidate"]
    attempted: bool = False
    # A provider call actually reached the wire (budget declines and
    # input-envelope rejections never did).
    model_called: bool = False
    failure_code: str | None = None
    claim_count: int = 0
    persistable_claims: list[ReflectionClaim] = field(default_factory=list)
    validation_issues: list[str] = field(default_factory=list)
    value_reason_codes: list[str] = field(default_factory=list)


@dataclass
class RecoveryLessonResult:
    episodes: list[RecoveryEpisode] = field(default_factory=list)
    outcomes: list[EpisodeLessonOutcome] = field(default_factory=list)
    # Lessons that passed the shared validator and value gate, ready for the
    # idempotent MemoryPipeline submit. Claim IDs are namespaced per episode
    # so independent lessons never collide.
    lessons: list[ReflectionClaim] = field(default_factory=list)
    # Validated but not persisted observations (e.g. a candidate episode's
    # inferred warning the whole-task value gate declines). Kept for shadow
    # comparison; they are not durable lessons.
    observations: list[ReflectionClaim] = field(default_factory=list)

    @property
    def llm_called(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.model_called)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_count": len(self.episodes),
            "llm_called": self.llm_called,
            "episodes": [
                {
                    "episode_id": episode.episode_id,
                    "causal_status": episode.causal_status,
                    "related_error_ids": list(episode.related_error_ids),
                    "failed_invocations": [
                        call.invocation
                        for call in episode.failed_calls
                        if call.invocation
                    ],
                    "followup_invocations": [
                        call.invocation
                        for call in episode.followup_calls
                        if call.invocation
                    ],
                    "recovery_id": episode.recovery.recovery_id
                    if episode.recovery
                    else None,
                    "verification_ids": [
                        item.verification_id for item in episode.verifications
                    ],
                }
                for episode in self.episodes
            ],
            "outcomes": [
                {
                    "episode_id": outcome.episode_id,
                    "causal_status": outcome.causal_status,
                    "attempted": outcome.attempted,
                    "model_called": outcome.model_called,
                    "failure_code": outcome.failure_code,
                    "claim_count": outcome.claim_count,
                    "persistable_claim_ids": [
                        claim.claim_id for claim in outcome.persistable_claims
                    ],
                    "validation_issues": list(outcome.validation_issues),
                    "value_reason_codes": list(outcome.value_reason_codes),
                }
                for outcome in self.outcomes
            ],
            "lessons": [claim.to_dict() for claim in self.lessons],
            "observations": [claim.to_dict() for claim in self.observations],
        }


def _episode_verifications(
    recovery: RecoveryEvidence,
    evidence: TaskEvidence,
) -> list[VerificationEvidence]:
    related_errors = [
        item for item in evidence.errors if item.error_id in recovery.related_error_ids
    ]
    return [
        item
        for item in _passed_verifications_after_recovery(recovery, evidence)
        if _verification_evidence_matches_recovery(
            item, recovery, related_errors, evidence
        )
    ]


def _episode_event_ids(
    errors: list[ErrorEvidence],
    recovery: RecoveryEvidence | None,
    calls: list[ToolEvidence],
    verifications: list[VerificationEvidence],
) -> tuple[str, ...]:
    ids: list[str] = []
    for error in errors:
        ids.extend(error.source_event_ids)
    for call in calls:
        ids.append(call.call_event_id)
        ids.extend(call.result_event_ids)
    if recovery is not None:
        ids.extend(recovery.event_ids)
    for item in verifications:
        ids.extend(item.event_ids)
    ordered: list[str] = []
    for item in ids:
        if item and item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def _invocation_operands(invocation: str) -> set[str]:
    """Business tokens of an invocation: executable and operands, no flags."""
    objective = call_objective(invocation)
    return {objective.executable, *objective.bare_operands}


def _same_operation_objective(failed_invocation: str, invocation: str) -> bool:
    """Same operation through the shared CallObjective contract.

    ``python scripts/cleanup.py`` does not act on the same object as
    ``python scripts/report.py`` however much they share an interpreter;
    only an identical selection scope and a covered input target qualify a
    retry for the episode.
    """
    return objectives_same_target(
        call_objective_from_rendered(failed_invocation),
        call_objective_from_rendered(invocation),
    )


def extract_recovery_episodes(evidence: TaskEvidence) -> list[RecoveryEpisode]:
    """Slice the trace into bounded recovery episodes for per-segment review.

    Two feeds: established recoveries with their linked checks, and the
    changed-input retries the rule path deliberately declines to confirm
    (transient failures included). The second feed is what the review called
    "keep it for the learner" -- most prominently a ``timeout`` budget raise,
    which disappears entirely from the rule projection.
    """
    episodes: list[RecoveryEpisode] = []
    claimed_errors = {
        error_id
        for recovery in evidence.recoveries
        for error_id in recovery.related_error_ids
    }
    calls_by_id = {call.call_id: call for call in evidence.tool_calls if call.call_id}

    for recovery in evidence.recoveries[:EPISODE_MAX_ITEMS]:
        related_errors = [
            item
            for item in evidence.errors
            if item.error_id in recovery.related_error_ids
        ]
        if not related_errors:
            continue
        verifications = _episode_verifications(recovery, evidence)
        involved_calls = [
            calls_by_id[error.call_id]
            for error in related_errors
            if error.call_id and error.call_id in calls_by_id
        ]
        verify_calls = [
            calls_by_id[item.call_id]
            for item in verifications
            if item.call_id and item.call_id in calls_by_id
        ]
        episodes.append(
            RecoveryEpisode(
                episode_id=f"episode-{len(episodes) + 1:06d}",
                related_error_ids=tuple(
                    error.error_id for error in related_errors
                ),
                failed_calls=tuple(involved_calls[:_EPISODE_MAX_CALLS]),
                followup_calls=tuple(
                    call
                    for call in verify_calls
                    if call not in involved_calls
                )[:_EPISODE_MAX_CALLS],
                recovery=recovery,
                verifications=tuple(verifications),
                event_ids=_episode_event_ids(
                    related_errors, recovery, involved_calls, verifications
                ),
                causal_status="verified" if verifications else "candidate",
            )
        )

    if len(episodes) >= EPISODE_MAX_ITEMS:
        return episodes

    # Changed-input retries the rule path will not confirm. Same tool, a
    # materially different invocation, later success -- including transient
    # failures, which the deterministic paths exclude precisely because a
    # changed input cannot prove causation for them. The learner sees the
    # observation; the shared gates keep it out of confirmed status.
    positions = evidence.event_positions
    for error in evidence.errors:
        if len(episodes) >= EPISODE_MAX_ITEMS:
            break
        if error.error_id in claimed_errors or not error.call_id:
            continue
        failed_call = calls_by_id.get(error.call_id)
        if failed_call is None or not failed_call.invocation:
            continue
        failed_position = positions.get(failed_call.call_event_id)
        if failed_position is None:
            continue
        # Only retries of the same operation qualify as follow-ups, judged
        # by the shared objective contract -- an unrelated successful call
        # in between (`python scripts/cleanup.py` next to
        # `python scripts/report.py`) shares an interpreter and nothing
        # else.  Relevance is decided before the size cap so noise events
        # cannot displace the real retry.
        followups = [
            call
            for call in evidence.tool_calls
            if call.tool_name == failed_call.tool_name
            and call.status == "success"
            and call.invocation
            and call.invocation != failed_call.invocation
            and failed_position < positions.get(call.call_event_id, failed_position)
            and _same_operation_objective(failed_call.invocation, call.invocation)
        ][:_EPISODE_MAX_FOLLOWUPS]
        if not followups:
            continue
        claimed_errors.add(error.error_id)
        episodes.append(
            RecoveryEpisode(
                episode_id=f"episode-{len(episodes) + 1:06d}",
                related_error_ids=(error.error_id,),
                failed_calls=(failed_call,),
                followup_calls=tuple(followups),
                recovery=None,
                verifications=(),
                event_ids=_episode_event_ids(
                    [error], None, [failed_call, *followups], []
                ),
                # Transient failures can never be more than a candidate;
                # nor can a retry whose success check the trace did not
                # record as a linked verification.
                causal_status="candidate",
            )
        )
    return episodes


def _episode_failed_check_context(
    episode: RecoveryEpisode,
    parent: TaskEvidence,
) -> list[VerificationEvidence]:
    """Earlier failed checks the episode's passing checks may have redone.

    The shared red-green proof compares a passing check against a failing
    one on the same target; dropping the failing half from the projection
    would demote a recovery the parent evidence confirms.  The failing
    checks bound to the recovery's own errors come first -- they are the
    red side and are always kept -- and only the remaining same-kind
    context is bounded.
    """
    if episode.recovery is None:
        return []
    kinds = {
        item.command_kind
        for item in episode.verifications
        if item.command_kind
    }
    if not kinds:
        return []
    recovery_position = max(
        (
            parent.event_positions[event_id]
            for event_id in episode.recovery.event_ids
            if event_id in parent.event_positions
        ),
        default=-1,
    )
    related_call_ids = {
        error.call_id
        for error in parent.errors
        if error.error_id in episode.recovery.related_error_ids and error.call_id
    }
    primary: list[VerificationEvidence] = []
    extra: list[VerificationEvidence] = []
    for item in parent.verification:
        if item.result != "failed" or item.command_kind not in kinds:
            continue
        position = max(
            (
                parent.event_positions[event_id]
                for event_id in item.event_ids
                if event_id in parent.event_positions
            ),
            default=-1,
        )
        if not (recovery_position < 0 or 0 <= position < recovery_position):
            continue
        # The red side of *this* recovery outranks unrelated failures: a
        # flood of noise checks must never consume its slot before the
        # relationship is found.
        if item.call_id and item.call_id in related_call_ids:
            primary.append(item)
        else:
            extra.append(item)
    return (primary + extra)[: max(_EPISODE_MAX_FAILED_CHECKS, len(primary))]


def episode_to_task_evidence(
    episode: RecoveryEpisode,
    parent: TaskEvidence,
) -> TaskEvidence:
    """Project one episode into the bounded evidence the synthesizer accepts.

    The projection keeps the minimal *complete* proof chain: the linked
    passing checks, the earlier failing checks of the same kind they may
    have redone (with their invocations), and the referenced files bound to
    the episode's calls. Admission of the same candidate must not differ
    between parent and episode evidence.
    """
    errors = [
        item for item in parent.errors if item.error_id in episode.related_error_ids
    ]
    calls = [
        call
        for call in (*episode.failed_calls, *episode.followup_calls)
        if call.call_id is not None
    ]
    verifications = [
        item
        for item in parent.verification
        if item in episode.verifications
    ]
    failed_context = _episode_failed_check_context(episode, parent)
    verifications.extend(
        item for item in failed_context if item not in verifications
    )
    known_call_ids = {call.call_id for call in calls}
    # Every verification kept by the episode must keep its invocation too:
    # the validator builds the relationship through that call, so a kept
    # check without its call would reject candidates the parent accepts.
    # Truncation applies to how many checks an episode keeps, never to the
    # proof chain of the checks it did keep.
    required_call_ids = {
        item.call_id
        for item in (*verifications, *failed_context)
        if item.call_id
    }
    calls.extend(
        call
        for call in parent.tool_calls
        if call.call_id
        and call.call_id in required_call_ids
        and call.call_id not in known_call_ids
    )
    episode_call_ids = known_call_ids | {
        item.call_id for item in verifications if item.call_id
    }
    referenced_files = [
        item
        for item in parent.referenced_files
        if item.call_id and item.call_id in episode_call_ids
    ]
    recoveries = (
        [episode.recovery] if episode.recovery is not None else []
    )
    event_ids = set(episode.event_ids)
    for item in verifications:
        event_ids.update(item.event_ids)
    for call in calls:
        event_ids.add(call.call_event_id)
        event_ids.update(call.result_event_ids)
    return TaskEvidence(
        tool_calls=calls,
        errors=errors,
        recoveries=recoveries,
        verification=verifications,
        referenced_files=referenced_files,
        outcome=parent.outcome,
        had_errors=bool(errors),
        errors_recovered=bool(episode.recovery),
        event_positions={
            event_id: position
            for event_id, position in parent.event_positions.items()
            if event_id in event_ids
        },
    )


class RecoveryLessonAgent:
    """Dedicated no-tools recovery learner, one bounded episode at a time.

    Reuses ``LLMReflectionSynthesizer`` (isolated context, structured
    candidate output, budget/timeout/cancellation control) so the lesson
    path inherits its privacy and reliability boundary instead of growing a
    second client. Per-episode generation means one weak segment cannot
    discard unrelated durable claims, and an abstained episode stays empty.
    """

    def __init__(
        self,
        client: StructuredGenerationClient,
        config: ReflectionLLMConfig,
        *,
        validator: ReflectionClaimValidator | None = None,
        value_gate: ReflectionValueGate | None = None,
    ) -> None:
        self._synthesizer = LLMReflectionSynthesizer(client, config)
        self._validator = validator or ReflectionClaimValidator()
        self._value_gate = value_gate or ReflectionValueGate()

    def attempt_lessons(
        self,
        task_description: str,
        evidence: TaskEvidence,
        *,
        agent_budget: Any | None = None,
        event_sink: Any | None = None,
        cancellation_token: Any | None = None,
        deadline_monotonic: float | None = None,
    ) -> RecoveryLessonResult:
        result = RecoveryLessonResult()
        result.episodes = extract_recovery_episodes(evidence)
        for episode in result.episodes:
            outcome = EpisodeLessonOutcome(
                episode_id=episode.episode_id,
                causal_status=episode.causal_status,
            )
            result.outcomes.append(outcome)
            sub_evidence = episode_to_task_evidence(episode, evidence)
            outcome.attempted = True
            attempt = self._synthesizer.attempt(
                task_description,
                sub_evidence,
                agent_budget=agent_budget,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
                deadline_monotonic=deadline_monotonic,
            )
            outcome.model_called = attempt.model_called
            if not attempt.success or attempt.candidate is None:
                outcome.failure_code = attempt.failure_code
                continue
            candidate: ReflectionCandidate = attempt.candidate
            outcome.claim_count = len(candidate.claims)
            validation: ClaimValidationResult = self._validator.validate(
                candidate, sub_evidence
            )
            outcome.validation_issues = sorted(
                {
                    issue.code
                    for issue in validation.issues
                    if issue.severity == "error"
                }
            )
            value: ReflectionValueDecision = self._value_gate.evaluate(
                candidate, validation, sub_evidence
            )
            outcome.value_reason_codes = list(value.reason_codes)

            def namespaced(claim: ReflectionClaim) -> ReflectionClaim:
                return replace(
                    claim,
                    claim_id=f"{episode.episode_id}:{claim.claim_id}",
                )

            accepted_ids = set(value.accepted_claim_ids)
            for claim in validation.valid_claims:
                if value.accepted and claim.claim_id in accepted_ids:
                    lesson = namespaced(claim)
                    outcome.persistable_claims.append(lesson)
                    result.lessons.append(lesson)
                else:
                    # Validated but not selected for persistence: an
                    # observation for shadow review, never a durable lesson.
                    result.observations.append(namespaced(claim))
        return result


__all__ = [
    "EpisodeLessonOutcome",
    "RecoveryEpisode",
    "RecoveryLessonAgent",
    "RecoveryLessonResult",
    "episode_to_task_evidence",
    "extract_recovery_episodes",
]
