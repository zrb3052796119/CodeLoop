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
from minicode.skill_description_page import DescriptionRow, render_description_page
from minicode.skill_directory_presel import (
    blend_directory_suggestions,
    directory_presel_text,
)
from minicode.skill_router import (
    DEFAULT_SKILL_TOP_K,
    SkillRouter,
    SkillRoutingResult,
    resolve_skill_authority_only,
)
from minicode.skill_semantics import AliasSemanticMatcher
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
    # Advisory directory-level semantic preselection (hierarchical mode
    # only). Never touches prompt_skills, policy, or authority.
    directory_presel: Any = None


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


def _needs_clarification(
    request: SkillRoutingTurnRequest, intent: ParsedIntent
) -> bool:
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


_DEFAULT_FLAT_CATALOG_CHARS = 8000
# Mirror of skillSelection.skillPageCharBudget for hint prefetch capping;
# config.py owns the authoritative clamp, this is only the fail-closed default.
_DEFAULT_SKILL_PAGE_CHARS = 4000

_MAX_HINT_STAGES = 4
_MAX_HINT_ENTITIES = 6
_MAX_HINT_CONSTRAINTS = 4
_MAX_HINT_DIRECTORIES = 3
_MAX_HINT_EVIDENCE_CHARS = 80
# Only used when an injected presel object forgot to publish its calibrated
# threshold; production matchers always carry the provider-calibrated value.
_PRESSEL_FALLBACK_THRESHOLD = 0.60


def _controls_char_budget(
    controls: Mapping[str, Any] | None,
    key: str,
    default: int,
) -> int:
    """Read a positive char budget from turn controls, failing closed."""
    try:
        parsed = int((controls or {}).get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _controls_bool(controls: Mapping[str, Any] | None, key: str) -> bool:
    """Read a boolean flag from turn controls, failing closed to False."""
    value = (controls or {}).get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _skill_field(skill: object, key: str) -> str:
    if isinstance(skill, Mapping):
        return str(skill.get(key) or "")
    return str(getattr(skill, key, "") or "")


def _operation_polarity_value(operation: object) -> str:
    polarity = getattr(operation, "polarity", None)
    return str(getattr(polarity, "value", polarity) or "")


def _operation_evidence_quote(operation: object, raw_input: str) -> str:
    """Short user-text quote for an operation's span, when it has one."""
    span = getattr(operation, "span", None)
    if not span or len(span) != 2 or not all(isinstance(point, int) for point in span):
        return ""
    start, end = span
    if not (0 <= start < end <= len(raw_input)):
        return ""
    return raw_input[start:end].strip()[:_MAX_HINT_EVIDENCE_CHARS]


def _build_intent_hints(
    intent: ParsedIntent,
    prompt_skills: tuple[object, ...],
    *,
    prefetch: bool,
    page_chars: int,
    presel: Any = None,
) -> dict[str, Any]:
    """Advisory hint payload derived from the already-parsed intent.

    Every field is a correctable suggestion for hierarchical discovery: the
    model still sees the original request and the full directory cards, and
    nothing here changes prompt_skills, the load policy, or authority.
    """
    raw_input = str(getattr(intent, "raw_input", "") or "")
    operations = tuple(getattr(intent, "operations", ()) or ())
    affirmed = [
        operation
        for operation in operations
        if _operation_polarity_value(operation) == "affirmed"
    ]
    negated = [
        operation
        for operation in operations
        if _operation_polarity_value(operation) == "negated"
    ]

    stages: list[dict[str, str]] = []
    for operation in affirmed[:_MAX_HINT_STAGES]:
        stage = {"operation": str(getattr(operation, "clause", "") or "")}
        quote = _operation_evidence_quote(operation, raw_input)
        if quote:
            stage["evidence"] = quote
        stages.append(stage)

    entities: list[str] = []
    entity_groups = getattr(intent, "entities", None) or {}
    if isinstance(entity_groups, Mapping):
        for values in entity_groups.values():
            for value in values or ():
                text = str(value)
                if text and text not in entities:
                    entities.append(text)
                if len(entities) >= _MAX_HINT_ENTITIES:
                    break
            if len(entities) >= _MAX_HINT_ENTITIES:
                break

    constraints = [
        str(getattr(operation, "clause", "") or "")
        for operation in negated[:_MAX_HINT_CONSTRAINTS]
    ]

    # Cheap lexical ranking of directories: share of the query's alias
    # concept groups that each directory's name + description also covers.
    # Zero-coverage directories are excluded honestly — an empty list just
    # means the matcher found nothing.
    matcher = AliasSemanticMatcher()
    query_text = " ".join(
        [raw_input, *entities]
        + [str(getattr(operation, "clause", "") or "") for operation in affirmed]
    )
    by_directory: dict[str, str] = {}
    skill_names_by_directory: dict[str, list[str]] = {}
    for skill in prompt_skills:
        directory = _skill_field(skill, "directory")
        if not directory:
            continue
        if directory not in by_directory:
            by_directory[directory] = _skill_field(skill, "directory_description")
            skill_names_by_directory[directory] = []
        skill_names_by_directory[directory].append(
            _skill_field(skill, "qualified_name") or _skill_field(skill, "name")
        )
    scored = sorted(
        (
            (
                matcher.query_coverage(query_text, f"{directory} {description}"),
                directory,
            )
            for directory, description in by_directory.items()
        ),
        key=lambda item: (-item[0], item[1]),
    )
    suggested = [
        directory
        for coverage, directory in scored[:_MAX_HINT_DIRECTORIES]
        if coverage > 0.0
    ]
    directory_sources: dict[str, str] = {}
    if presel is not None:
        # Semantic preselection is advisory and fail-closed: a transport
        # failure leaves the lexical ranking untouched. Lexical-only names
        # keep the original ordering; semantic candidates may join them when
        # the lexical pass missed the directory's vocabulary entirely.
        lexical_scores = {
            directory: coverage for coverage, directory in scored if coverage > 0.0
        }
        try:
            # The embedding text lists the directory's skill names: hidden
            # qualified-name vocabulary is what lexical matching on the card
            # alone cannot see.
            presel_texts = {
                directory: directory_presel_text(
                    directory,
                    by_directory[directory],
                    skill_names_by_directory.get(directory, []),
                )
                for directory in by_directory
            }
            semantic_scores = presel.rank(query_text, presel_texts)
            suggested, directory_sources = blend_directory_suggestions(
                lexical_scores,
                semantic_scores,
                signal_threshold=getattr(
                    presel, "signal_threshold", _PRESSEL_FALLBACK_THRESHOLD
                ),
                limit=_MAX_HINT_DIRECTORIES,
            )
        except Exception as error:  # pragma: no cover - defensive fail-closed
            logger.warning(
                "Directory presel failed (%s); lexical suggestions only", error
            )

    hints: dict[str, Any] = {
        "hinted": True,
        "stages": stages,
        "entities": entities,
        "constraints": constraints,
        "suggested_directories": suggested,
        "confidence": float(getattr(intent, "confidence", 0.0) or 0.0),
    }
    if directory_sources:
        hints["suggested_directory_sources"] = directory_sources

    if prefetch and suggested:
        top = suggested[0]
        # Same line shape and ordering as browse_skill_directory pages; the
        # catalog is already policy-filtered for this turn.
        entries = sorted(
            (
                skill
                for skill in prompt_skills
                if _skill_field(skill, "directory") == top
            ),
            key=lambda skill: (
                _skill_field(skill, "qualified_name") or _skill_field(skill, "name")
            ),
        )
        rows = []
        for skill in entries:
            name = _skill_field(skill, "qualified_name") or _skill_field(skill, "name")
            rows.append(
                DescriptionRow(
                    name=name,
                    prefix=f"- {name}: ",
                    description=_skill_field(skill, "description"),
                    section=f"## {top} ({len(entries)} skills)",
                )
            )
        page = render_description_page(
            rows,
            offset=0,
            limit=50,
            budget=page_chars,
            tool_name="browse_skill_directory",
            leading_lines=(
                "Prefetched descriptions (advisory first page; "
                "other directories remain available):",
            ),
        )
        if page.ok and page.rendered:
            hints["prefetched_directory"] = top
            # This complete block is the prompt's canonical projection: the
            # advisory heading and continuation marker are inside its budget.
            hints["prefetched_page"] = page.output
            hints["prefetch_char_budget"] = page_chars
            hints["prefetched_skills"] = [
                line for line in page.output.splitlines() if line.startswith("- ")
            ]
            hints["prefetch_truncated"] = page.truncated
            if page.truncated:
                hints["prefetch_next_offset"] = page.rendered

    return hints


def _model_first_decision(
    request: SkillRoutingTurnRequest,
    intent: ParsedIntent,
    *,
    flat_catalog_chars: int = _DEFAULT_FLAT_CATALOG_CHARS,
    controls: Mapping[str, Any] | None = None,
    directory_presel: Any = None,
) -> SkillRoutingTurnDecision:
    """Prepare the full authorized catalog without any relevance scoring.

    Authority resolution and constraint vetoes still run because they depend
    only on the raw input and the catalog. When the estimated flat rendering
    exceeds ``flat_catalog_chars`` and at least one visible skill lives in a
    SKILL_DIR directory, the prompt projection escalates to the hierarchical
    directory-card form; the load policy is catalog-scoped either way.

    Experiment arm B: when ``controls["skill_intent_hints"]`` is on and the
    projection escalated to hierarchical, an advisory intent-hint payload is
    attached to the routing result. Flat ``model`` mode never carries hints —
    they target directory discovery, not the flat catalog. Hints are
    presentation-only: prompt_skills, policy, and authority are untouched.
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
                or (skill.get("qualified_name") if isinstance(skill, Mapping) else "")
                or getattr(skill, "name", None)
                or (skill.get("name") if isinstance(skill, Mapping) else "")
                or ""
            )
            not in constraint_vetoed
        )
        prompt_mode = "model"
        # 8 chars approximate the "- " prefix, ": " separator, and newline of
        # one flat catalog line.
        flat_estimate = sum(
            len(_skill_field(skill, "qualified_name") or _skill_field(skill, "name"))
            + len(_skill_field(skill, "description"))
            + 8
            for skill in prompt_skills
        )
        if flat_estimate > flat_catalog_chars and any(
            _skill_field(skill, "directory") for skill in prompt_skills
        ):
            prompt_mode = "hierarchical"
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

    # Experiment arm B: prefetch implies hints (same contract as config.py),
    # and hints only apply to the hierarchical directory-card projection.
    hints_enabled = _controls_bool(controls, "skill_intent_hints") or _controls_bool(
        controls, "skill_intent_prefetch"
    )
    if hints_enabled and prompt_mode == "hierarchical":
        try:
            routing.intent_hints = _build_intent_hints(
                intent,
                prompt_skills,
                prefetch=_controls_bool(controls, "skill_intent_prefetch"),
                page_chars=_controls_char_budget(
                    controls, "skill_page_chars", _DEFAULT_SKILL_PAGE_CHARS
                ),
                presel=directory_presel,
            )
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
    mode = str((base_controls or {}).get("skill_selection_mode", "routed") or "routed")

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
        # skipped. Over-budget catalogs with directories render as directory
        # cards instead of a flat list.
        return _model_first_decision(
            request,
            intent,
            flat_catalog_chars=_controls_char_budget(
                base_controls,
                "skill_flat_catalog_chars",
                _DEFAULT_FLAT_CATALOG_CHARS,
            ),
            controls=base_controls,
            directory_presel=(
                adapters.directory_presel
                if _controls_bool(base_controls, "skill_directory_presel")
                else None
            ),
        )

    routing_context: dict[str, Any] | None = None
    if base_controls is not None or adapters.candidate_reranker is not None:
        routing_context = dict(base_controls or {})
        routing_context["purpose"] = "skill-routing"
        routing_context.setdefault("preserve_model_calls", 1)
        if adapters.candidate_reranker is not None:
            routing_context["skill_candidate_reranker"] = adapters.candidate_reranker

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
        for name in (getattr(routing, "constraint_vetoed_names", ()) or ())
        if str(name)
    )
    explicit_constraint_conflict = bool(
        constraint_vetoed.intersection(str(name) for name in requested)
    )
    used_fallback = bool(getattr(routing, "used_fallback", False))
    if deny_all or explicit_constraint_conflict:
        prompt_skills = ()
        prompt_mode = (
            "constraint_conflict" if explicit_constraint_conflict else "suppressed"
        )
    elif used_fallback:
        prompt_skills = tuple(
            skill
            for skill in request.skills
            if str(
                getattr(skill, "qualified_name", None)
                or (skill.get("qualified_name") if isinstance(skill, Mapping) else "")
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
    elif (
        getattr(
            getattr(intent, "diagnostics", None),
            "semantic_status",
            "disabled",
        )
        == "abstained"
    ):
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
