"""Canonical parse-and-route composition for one Agent Turn.

Production entry points and offline evaluators cross this same seam.  The
module owns call-context separation, canonical Intent reuse, Skill routing,
and the final prompt projection.  Provider adapters remain injected and the
offline evaluator can therefore exercise the production-shaped path without
creating a model or making a remote call.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from minicode.capability_registry import CapabilityRegistry
from minicode.intent_parser import (
    IntentContext,
    IntentSemanticAdapter,
    ParsedIntent,
)
from minicode.skill_candidate_reranker import SkillCandidateReranker
from minicode.skill_router import (
    DEFAULT_SKILL_TOP_K,
    SkillRouter,
    SkillRoutingResult,
    resolve_skill_authority_only,
)
from minicode.tooling import skill_load_binding_from_value

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillRoutingTurnRequest:
    """Immutable inputs whose interpretation must match across all callers."""

    prompt: str
    skills: Sequence[object]
    registry: CapabilityRegistry
    context: IntentContext | None = None
    parsed_intent: ParsedIntent | None = None
    top_k: int = DEFAULT_SKILL_TOP_K

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str):
            raise TypeError("prompt must be a string")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int):
            raise TypeError("top_k must be an integer")
        if self.top_k < 1:
            raise ValueError("top_k must be positive")
        if isinstance(self.skills, (str, bytes)) or not isinstance(
            self.skills, Sequence
        ):
            raise TypeError("skills must be a sequence")
        object.__setattr__(self, "skills", tuple(self.skills))


@dataclass(frozen=True, slots=True)
class SkillRoutingTurnAdapters:
    """Injected implementations at the parser and candidate-rerank seams."""

    router: SkillRouter
    semantic_intent: IntentSemanticAdapter | None = None
    candidate_reranker: SkillCandidateReranker | None = None


@dataclass(frozen=True, slots=True)
class SkillRoutingTurnDecision:
    """Canonical Intent, routing result, and effective prompt projection."""

    intent: ParsedIntent
    routing: SkillRoutingResult
    prompt_skills: tuple[object, ...]
    prompt_mode: str
    outcome: str
    outcome_reason: str


_UNBOUND_REFERENCE_RE = re.compile(
    r"\b(?:it|this|that|them|those|same|above|previous)\b|"
    r"(?:它|这个|那个|这个呢|这段|该文件|刚才|上面|前述)",
    re.IGNORECASE,
)
_PURE_CONTEXT_REFERENCE_RE = re.compile(
    r"^\s*(?:same\s+as\s+above|same\s+(?:thing|one)|"
    r"照旧|还是这个|跟上面一样|就按那个来)[.!?。！？]*\s*$",
    re.IGNORECASE,
)
_VAGUE_REFERENCE_ACTION_RE = re.compile(
    r"(?:处理|弄|搞|改|修|做)(?:一?下|掉|好)?(?:吧)?[。！？!?]?\s*$|"
    r"(?:再来|重来)(?:一遍|一次|一下)?(?:吧)?[。！？!?]?\s*$|"
    r"\b(?:handle|fix|change|do)\s+(?:it|this|that)\b",
    re.IGNORECASE,
)
_IDENTIFIER_NOISE_RE = re.compile(
    r"^[\W_]*(?:[a-z]{2,}[-_]\d+|[a-z]*\d+[a-z0-9_-]*)[\W_]*$",
    re.IGNORECASE,
)


def _needs_clarification(request: SkillRoutingTurnRequest, intent: ParsedIntent) -> bool:
    if tuple(getattr(intent, "operations", ()) or ()):
        return False
    if bool(getattr(intent, "context_used", False)):
        return False
    text = request.prompt.strip()
    return bool(
        _PURE_CONTEXT_REFERENCE_RE.fullmatch(text)
        or (
            _UNBOUND_REFERENCE_RE.search(text)
            and (
                _VAGUE_REFERENCE_ACTION_RE.search(text)
                or text.rstrip("。！？!? ").endswith(("呢",))
            )
        )
    )


def _looks_like_identifier_noise(text: str) -> bool:
    return _IDENTIFIER_NOISE_RE.fullmatch(text.strip()) is not None


def _model_first_decision(
    request: SkillRoutingTurnRequest,
    intent: ParsedIntent,
) -> SkillRoutingTurnDecision:
    """Prepare the full authorized catalog without any relevance scoring.

    Authority resolution and constraint vetoes still run because they depend
    only on the raw input and the catalog. Catalogs of any size stay in
    model mode because the resulting load policy is catalog-scoped rather
    than enumerated.
    """

    authority, constraint_vetoed_names = resolve_skill_authority_only(
        list(request.skills), intent
    )
    catalog_bindings = frozenset(
        binding
        for skill in request.skills
        if (binding := skill_load_binding_from_value(skill)) is not None
    )

    intent_type = getattr(intent, "intent_type", None)
    action_type = getattr(intent, "action_type", None)
    routing = SkillRoutingResult(
        intent_type=str(getattr(intent_type, "value", intent_type) or "unknown"),
        action_type=str(getattr(action_type, "value", action_type) or "unknown"),
        capability_domains=[],
        capability_scopes=[],
        selected=[],
        total_skills=len(request.skills),
        used_fallback=False,
        decision_source="model-first",
        authority_resolution=authority,
        catalog_bindings=catalog_bindings,
        constraint_vetoed_names=constraint_vetoed_names,
    )
    constraint_vetoed = frozenset(constraint_vetoed_names)
    if authority.deny_all:
        prompt_skills: tuple[object, ...] = ()
        prompt_mode = "suppressed"
        outcome = "no_skill"
        outcome_reason = "user-denied-all-skills"
    else:
        prompt_skills = tuple(
            skill
            for skill in request.skills
            if str(
                getattr(skill, "qualified_name", None)
                or (
                    skill.get("qualified_name")
                    if isinstance(skill, Mapping)
                    else ""
                )
                or getattr(skill, "name", None)
                or (skill.get("name") if isinstance(skill, Mapping) else "")
                or ""
            )
            not in constraint_vetoed
        )
        prompt_mode = "model"
        outcome = "route"
        # Unresolved/ambiguous explicit references stay observable but do
        # not close loading: the model corrects them against the catalog.
        outcome_reason = "model-catalog-selection"
        if authority.unresolved:
            outcome_reason += "-unresolved-not-enforced"
        if authority.ambiguous:
            outcome_reason += "-ambiguous-not-enforced"

    try:
        routing.turn_outcome = outcome
        routing.outcome_reason = outcome_reason
        routing.prompt_mode = prompt_mode
    except (AttributeError, TypeError):
        pass

    return SkillRoutingTurnDecision(
        intent=intent,
        routing=routing,
        prompt_skills=prompt_skills,
        prompt_mode=prompt_mode,
        outcome=outcome,
        outcome_reason=outcome_reason,
    )


def route_skill_turn(
    request: SkillRoutingTurnRequest,
    adapters: SkillRoutingTurnAdapters,
    *,
    controls: Mapping[str, Any] | None = None,
) -> SkillRoutingTurnDecision:
    """Parse and route one Turn while preserving provider authority limits.

    ``controls`` is the existing Agent-Turn budget/cancellation/event mapping.
    Intent classification receives it without a routing purpose.  Candidate
    reranking receives an isolated copy with the established purpose and
    model-call reservation.  A caller-provided ``parsed_intent`` is reused by
    identity, so downstream work-chain construction cannot silently reparse.
    ``controls["skill_selection_mode"] == "model"`` selects model-first
    catalog preparation and bypasses the router entirely.
    """

    if not isinstance(adapters, SkillRoutingTurnAdapters):
        raise TypeError("adapters must be SkillRoutingTurnAdapters")
    base_controls = dict(controls) if controls is not None else None
    mode = str(
        (base_controls or {}).get("skill_selection_mode", "routed") or "routed"
    )

    if request.parsed_intent is not None:
        intent = request.parsed_intent
    else:
        # Import at the seam so existing monkeypatch-based integration tests
        # and alternative parser adapters observe the same call as production.
        from minicode.intent_parser import parse_intent

        intent_kwargs: dict[str, Any] = {}
        if request.context is not None:
            intent_kwargs["context"] = request.context
        if adapters.semantic_intent is not None:
            intent_kwargs["semantic_adapter"] = adapters.semantic_intent
        if base_controls is not None:
            intent_kwargs["call_context"] = dict(base_controls)
        intent = parse_intent(request.prompt, **intent_kwargs)

    if mode == "model":
        # Model-first selection: the full authorized catalog goes to the
        # prompt and the load policy; scoring, embeddings, and reranking are
        # skipped.
        return _model_first_decision(request, intent)

    routing_context: dict[str, Any] | None = None
    if base_controls is not None or adapters.candidate_reranker is not None:
        routing_context = dict(base_controls or {})
        routing_context["purpose"] = "skill-routing"
        routing_context.setdefault("preserve_model_calls", 1)
        if adapters.candidate_reranker is not None:
            routing_context["skill_candidate_reranker"] = (
                adapters.candidate_reranker
            )

    route_kwargs: dict[str, Any] = {}
    if request.top_k != DEFAULT_SKILL_TOP_K:
        route_kwargs["top_k"] = request.top_k
    if routing_context is not None:
        route_kwargs["call_context"] = routing_context
    routing = adapters.router.route(
        list(request.skills),
        intent,
        request.registry,
        **route_kwargs,
    )

    authority = getattr(routing, "authority_resolution", None)
    deny_all = bool(getattr(authority, "deny_all", False))
    unresolved = tuple(getattr(authority, "unresolved", ()) or ())
    ambiguous = tuple(getattr(authority, "ambiguous", ()) or ())
    requested = tuple(getattr(authority, "requested", ()) or ())
    constraint_vetoed = frozenset(
        str(name)
        for name in (
            getattr(routing, "constraint_vetoed_names", ()) or ()
        )
        if str(name)
    )
    explicit_constraint_conflict = bool(
        constraint_vetoed.intersection(str(name) for name in requested)
    )
    used_fallback = bool(getattr(routing, "used_fallback", False))
    if deny_all or explicit_constraint_conflict:
        prompt_skills = ()
        prompt_mode = (
            "constraint_conflict"
            if explicit_constraint_conflict
            else "suppressed"
        )
    elif used_fallback:
        prompt_skills = tuple(
            skill
            for skill in request.skills
            if str(
                getattr(skill, "qualified_name", None)
                or (
                    skill.get("qualified_name")
                    if isinstance(skill, Mapping)
                    else ""
                )
                or getattr(skill, "name", None)
                or (skill.get("name") if isinstance(skill, Mapping) else "")
                or ""
            )
            not in constraint_vetoed
        )
        prompt_mode = "inventory_names"
    else:
        prompt_skills = tuple(routing.selected_skill_dicts())
        prompt_mode = "routed"

    selected = getattr(routing, "selected", None)
    if selected is None:
        selected = getattr(routing, "selected_skills", ())
    if deny_all:
        outcome = "no_skill"
        outcome_reason = "user-denied-all-skills"
    elif explicit_constraint_conflict:
        outcome = "clarify"
        outcome_reason = "explicit-skill-operation-conflict"
    elif unresolved:
        outcome = "clarify"
        outcome_reason = "explicit-skill-unresolved"
    elif ambiguous:
        outcome = "clarify"
        outcome_reason = "explicit-skill-ambiguous"
    elif selected:
        outcome = "route"
        outcome_reason = "skills-selected"
    elif (
        getattr(routing, "candidate_count", 0)
        and getattr(routing, "reranker_status", "disabled") == "abstained"
    ):
        outcome = "abstain"
        outcome_reason = "candidate-reranker-abstained"
    elif getattr(
        getattr(intent, "diagnostics", None),
        "semantic_status",
        "disabled",
    ) == "abstained":
        outcome = "abstain"
        outcome_reason = "semantic-intent-abstained"
    elif _needs_clarification(request, intent):
        outcome = "clarify"
        outcome_reason = "unbound-reference"
    elif _looks_like_identifier_noise(request.prompt):
        outcome = "abstain"
        outcome_reason = "uninterpretable-input"
    else:
        outcome = "no_skill"
        outcome_reason = "no-catalog-match"

    # Low-cardinality outcome metadata is useful to prompt construction but
    # unresolved user identifiers remain only in the in-memory authority
    # object and never cross telemetry or prompt serialization boundaries.
    try:
        routing.turn_outcome = outcome
        routing.outcome_reason = outcome_reason
        routing.prompt_mode = prompt_mode
    except (AttributeError, TypeError):
        pass

    return SkillRoutingTurnDecision(
        intent=intent,
        routing=routing,
        prompt_skills=prompt_skills,
        prompt_mode=prompt_mode,
        outcome=outcome,
        outcome_reason=outcome_reason,
    )


__all__ = [
    "SkillRoutingTurnAdapters",
    "SkillRoutingTurnDecision",
    "SkillRoutingTurnRequest",
    "route_skill_turn",
]
