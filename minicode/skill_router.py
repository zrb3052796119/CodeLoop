from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import ActionType, IntentType, ParsedIntent

logger = logging.getLogger(__name__)


DEFAULT_SKILL_TOP_K = 5

# A classified intent still needs a minimum confidence before its
# intent/action label may create routing signal. Low-confidence guesses must
# rely on keyword/entity/semantic evidence instead.
_MIN_CONFIDENCE_INTENT_SIGNAL = 0.5

_SOURCE_PRIORITY = {
    "project": 3,
    "user": 2,
    "compat_project": 1,
    "compat_user": 0,
}

_SOURCE_BONUS = {
    "project": 0.3,
    "user": 0.2,
    "compat_project": 0.1,
    "compat_user": 0.0,
}

_INTENT_DOMAINS: dict[IntentType, set[CapabilityDomain]] = {
    IntentType.CODE: {CapabilityDomain.CODE, CapabilityDomain.FILE, CapabilityDomain.SEARCH},
    IntentType.DEBUG: {CapabilityDomain.CODE, CapabilityDomain.FILE, CapabilityDomain.SEARCH, CapabilityDomain.EXECUTION},
    IntentType.REFACTOR: {CapabilityDomain.CODE, CapabilityDomain.FILE, CapabilityDomain.SEARCH},
    IntentType.EXPLAIN: {CapabilityDomain.CODE, CapabilityDomain.FILE, CapabilityDomain.SEARCH, CapabilityDomain.ANALYSIS},
    IntentType.SEARCH: {CapabilityDomain.SEARCH, CapabilityDomain.FILE, CapabilityDomain.CODE},
    IntentType.REVIEW: {CapabilityDomain.CODE, CapabilityDomain.FILE, CapabilityDomain.SEARCH, CapabilityDomain.ANALYSIS},
    IntentType.TEST: {CapabilityDomain.EXECUTION, CapabilityDomain.CODE, CapabilityDomain.FILE},
    IntentType.DOCUMENT: {CapabilityDomain.FILE, CapabilityDomain.CODE},
    IntentType.CONFIGURE: {CapabilityDomain.FILE, CapabilityDomain.SYSTEM, CapabilityDomain.EXECUTION},
    IntentType.MEMORY: {CapabilityDomain.MEMORY},
}

_ACTION_SCOPES: dict[ActionType, set[CapabilityScope]] = {
    ActionType.READ: {CapabilityScope.READONLY},
    ActionType.ANALYZE: {CapabilityScope.READONLY},
    ActionType.CREATE: {CapabilityScope.READONLY, CapabilityScope.WRITE},
    ActionType.UPDATE: {CapabilityScope.READONLY, CapabilityScope.WRITE},
    ActionType.DELETE: {CapabilityScope.WRITE, CapabilityScope.DESTRUCTIVE},
    ActionType.EXECUTE: {CapabilityScope.READONLY, CapabilityScope.DESTRUCTIVE, CapabilityScope.EXTERNAL},
}

_DOMAIN_TERMS = {
    CapabilityDomain.FILE: {"file", "files", "filesystem", "path", "read", "write"},
    CapabilityDomain.CODE: {"code", "coding", "implementation", "architecture", "review", "diff"},
    CapabilityDomain.SEARCH: {"search", "grep", "find", "locate", "lookup"},
    CapabilityDomain.WEB: {"web", "http", "fetch", "network"},
    CapabilityDomain.SYSTEM: {"system", "config", "settings"},
    CapabilityDomain.MEMORY: {"memory", "remember", "profile"},
    CapabilityDomain.COMMUNICATION: {"communicate", "message", "ask"},
    CapabilityDomain.ANALYSIS: {"analysis", "analyze", "explain", "inspect"},
    CapabilityDomain.EXECUTION: {"execution", "execute", "run", "command", "shell", "test"},
}

_SCOPE_TERMS = {
    CapabilityScope.READONLY: {"readonly", "read-only", "read", "explain", "inspect", "analyze"},
    CapabilityScope.WRITE: {"write", "edit", "modify", "update", "create"},
    CapabilityScope.DESTRUCTIVE: {"destructive", "danger", "delete", "command", "execute", "run", "shell"},
    CapabilityScope.EXTERNAL: {"external", "web", "network", "http", "fetch"},
}

_GENERIC_ROUTING_KEYWORDS = frozenset(
    {"agent", "project", "skill", "skills", "workflow"}
)

# Semantic routing thresholds. An UNKNOWN intent needs more semantic evidence
# than a classified one before the abstention promise may be broken: at least
# two distinct recognized concepts AND most of the query's concepts covered.
# Small talk carries at most one stray development concept, so it stays out.
# Embedding similarity thresholds live on the SkillRouter instance (env
# overridable, calibrated per provider in skill_semantics).
_UNKNOWN_SEMANTIC_MIN_CONCEPTS = 2
_UNKNOWN_SEMANTIC_MIN_COVERAGE = 0.6
_EMBED_RETRY_COOLDOWN_SECONDS = 300.0
_MIN_SPECIFIC_SIGNAL = 1.0
_WEAK_SPECIFIC_SIGNAL_THRESHOLD = 2.0
_SPECIFIC_SIGNAL_MARGIN = 1.0
_MAX_EMBEDDING_CIRCUITS = 128


@dataclass(slots=True)
class _EmbeddingCircuitState:
    retry_after: float = 0.0
    in_flight: bool = False


_EMBEDDING_CIRCUIT_LOCK = threading.Lock()
_EMBEDDING_CIRCUITS: dict[tuple[str, str, str], _EmbeddingCircuitState] = {}


def _embedding_circuit_identity(
    matcher: object,
) -> tuple[str, str, str] | None:
    raw = getattr(matcher, "circuit_identity", None)
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            return None
    if (
        not isinstance(raw, (tuple, list))
        or len(raw) != 3
        or any(not isinstance(item, str) or not item for item in raw)
    ):
        return None
    return raw[0], raw[1], raw[2]


def _begin_embedding_attempt(
    identity: tuple[str, str, str], now: float
) -> bool:
    """Acquire one non-blocking workspace/provider embedding attempt."""
    with _EMBEDDING_CIRCUIT_LOCK:
        state = _EMBEDDING_CIRCUITS.get(identity)
        if state is not None and (state.in_flight or now < state.retry_after):
            return False
        if state is None:
            if len(_EMBEDDING_CIRCUITS) >= _MAX_EMBEDDING_CIRCUITS:
                removable = sorted(
                    (
                        (candidate.retry_after, key)
                        for key, candidate in _EMBEDDING_CIRCUITS.items()
                        if not candidate.in_flight
                    ),
                    key=lambda item: (item[0], item[1]),
                )
                if not removable:
                    return False
                _EMBEDDING_CIRCUITS.pop(removable[0][1], None)
            state = _EmbeddingCircuitState()
            _EMBEDDING_CIRCUITS[identity] = state
        state.in_flight = True
        state.retry_after = 0.0
        return True


def _finish_embedding_attempt(
    identity: tuple[str, str, str], *, retry_after: float
) -> None:
    with _EMBEDDING_CIRCUIT_LOCK:
        state = _EMBEDDING_CIRCUITS.get(identity)
        if state is None:
            return
        state.in_flight = False
        state.retry_after = max(0.0, retry_after)
        if state.retry_after == 0.0:
            _EMBEDDING_CIRCUITS.pop(identity, None)


@dataclass(slots=True)
class RoutedSkillDirectory:
    name: str
    description: str
    source: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class RoutedSkill:
    name: str
    description: str
    path: str
    source: str
    score: float
    reasons: list[str] = field(default_factory=list)
    qualified_name: str = ""
    directory: str = ""
    tools: list[str] = field(default_factory=list)
    content_digest: str = ""
    explicitly_requested: bool = False
    evidence_adjustment: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        projected = {
            "name": self.name,
            "qualified_name": self.qualified_name or self.name,
            "description": self.description,
            "path": self.path,
            "source": self.source,
            "score": self.score,
            "reasons": list(self.reasons),
            "directory": self.directory,
            "tools": list(self.tools),
            "content_digest": self.content_digest,
            "explicitly_requested": self.explicitly_requested,
        }
        if self.evidence_adjustment:
            projected["evidence_adjustment"] = self.evidence_adjustment
        return projected


@dataclass(slots=True)
class SkillRoutingResult:
    intent_type: str
    action_type: str
    capability_domains: list[str]
    capability_scopes: list[str]
    selected: list[RoutedSkill]
    total_skills: int
    used_fallback: bool
    selected_directories: list[RoutedSkillDirectory] = field(default_factory=list)
    selected_skills: list[RoutedSkill] = field(default_factory=list)
    tool_affinity: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        selected_skills = self.selected_skills or self.selected
        return {
            "intent_type": self.intent_type,
            "action_type": self.action_type,
            "capability_domains": list(self.capability_domains),
            "capability_scopes": list(self.capability_scopes),
            "selected": [skill.to_dict() for skill in self.selected],
            "selected_skills": [skill.to_dict() for skill in selected_skills],
            "selected_directories": [directory.to_dict() for directory in self.selected_directories],
            "tool_affinity": dict(self.tool_affinity),
            "total_skills": self.total_skills,
            "used_fallback": self.used_fallback,
        }

    def selected_skill_dicts(self) -> list[dict[str, Any]]:
        return [skill.to_dict() for skill in (self.selected_skills or self.selected)]

    def required_skill_names(self) -> list[str]:
        """Qualified bindings explicitly named by the user and therefore required.

        Semantic routing is advisory: a recommendation must not veto an
        otherwise correct final answer. Exact user references retain the
        strong load-before-final contract.
        """
        if self.used_fallback:
            return []
        return [
            skill.qualified_name or skill.name
            for skill in (self.selected_skills or self.selected)
            if skill.explicitly_requested and (skill.qualified_name or skill.name)
        ]


def required_skill_names_for_routing(routing: Any) -> list[str]:
    """Project required qualified names from current or legacy routing data."""
    if routing is None:
        return []
    if isinstance(routing, dict):
        if bool(routing.get("used_fallback", False)):
            return []
        selected = routing.get("selected_skills") or routing.get("selected") or []
    else:
        if bool(getattr(routing, "used_fallback", False)):
            return []
        projector = getattr(routing, "required_skill_names", None)
        if callable(projector):
            try:
                return [str(name) for name in projector() if str(name)]
            except Exception:  # noqa: BLE001 - legacy projection below is safe
                pass
        selected = (
            getattr(routing, "selected_skills", None)
            or getattr(routing, "selected", None)
            or []
        )
    selected_items = list(selected) if isinstance(selected, (list, tuple)) else []
    has_explicit_marker = any(
        (
            "explicitly_requested" in item
            or "explicitlyRequested" in item
        )
        if isinstance(item, dict)
        else hasattr(item, "explicitly_requested")
        for item in selected_items
    )
    names: list[str] = []
    for item in selected_items:
        if isinstance(item, dict):
            if has_explicit_marker and not bool(
                item.get("explicitly_requested", item.get("explicitlyRequested", False))
            ):
                continue
            name = item.get("qualified_name") or item.get("qualifiedName") or item.get("name")
        else:
            if has_explicit_marker and not bool(
                getattr(item, "explicitly_requested", False)
            ):
                continue
            name = (
                getattr(item, "qualified_name", None)
                or getattr(item, "qualifiedName", None)
                or getattr(item, "name", None)
            )
        normalized = str(name or "").strip()
        if normalized and normalized not in names:
            names.append(normalized)
    return names


def _skill_match_text(skill: dict) -> str:
    """The text a skill is matched against, shared by all routing layers."""
    return _normalize_text(
        " ".join(
            [
                str(skill.get("name", "")),
                str(skill.get("qualified_name") or skill.get("name", "")),
                str(skill.get("description", "")),
                str(skill.get("directory", "")),
                " ".join(_list_field(skill, "domains")),
                " ".join(_list_field(skill, "scopes")),
                " ".join(_list_field(skill, "keywords")),
                " ".join(_list_field(skill, "examples")),
                " ".join(_list_field(skill, "tools")),
            ]
        )
    )


def _skill_embed_key(skill: dict) -> str:
    digest = skill.get("content_digest")
    if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
        return digest
    content = skill.get("content")
    if isinstance(content, str) and content:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return hashlib.sha256(_skill_match_text(skill).encode("utf-8")).hexdigest()


def build_skill_router(workspace: str | Path | None = None) -> "SkillRouter":
    """Construct a SkillRouter, tolerating test doubles without kwargs.

    Integration tests monkeypatch ``SkillRouter`` with zero-argument
    stand-ins; routing must not break because the real constructor grew an
    optional embedding knob.
    """
    routing_feedback = None
    if workspace is not None:
        try:
            from minicode.skill_feedback import build_skill_routing_feedback

            routing_feedback = build_skill_routing_feedback(workspace)
        except Exception:  # noqa: BLE001 - feedback is an optional rank hint
            routing_feedback = None
    try:
        return SkillRouter(
            workspace=workspace,
            routing_feedback=routing_feedback,
        )
    except TypeError:
        return SkillRouter()


class SkillRouter:
    def __init__(
        self,
        *,
        workspace: str | Path | None = None,
        embedding_matcher: Any = None,
        routing_feedback: Any = None,
    ) -> None:
        from minicode.skill_semantics import (
            AliasSemanticMatcher,
            EmbeddingSemanticMatcher,
            embedding_thresholds,
        )

        self._alias = AliasSemanticMatcher()
        self._routing_feedback = routing_feedback
        self._embedding = embedding_matcher
        if self._embedding is None and workspace is not None:
            self._embedding = EmbeddingSemanticMatcher.from_environment(workspace)
        # Cosine distributions differ per embedding provider; thresholds are
        # env-overridable and default-calibrated for Qwen text-embedding-v3.
        (
            self._embed_signal_threshold,
            self._embed_signal_threshold_unknown,
            self._embed_boost_threshold,
        ) = embedding_thresholds(workspace)
        # After an embedding failure, stop calling the endpoint for a while
        # instead of paying the timeout on every routing turn.
        self._embedding_retry_after = 0.0

    def route(
        self,
        skills: list[dict],
        intent: ParsedIntent,
        registry: CapabilityRegistry,
        top_k: int = DEFAULT_SKILL_TOP_K,
    ) -> SkillRoutingResult:
        top_k = max(1, top_k)
        # Accept both the production dict projection and the public
        # discover_skills() dataclass shape at this boundary.
        skills = [_as_skill_dict(skill) for skill in skills]
        domains, scopes = self._relevant_capabilities(intent, registry)
        directories = self._score_directories(skills, intent, domains, scopes)
        selected_directories = [
            routed
            for routed, signal_score in sorted(
                (item for item in directories if item[1] > 0),
                key=lambda item: (
                    -item[0].score,
                    -_SOURCE_PRIORITY.get(item[0].source, -1),
                    item[0].name,
                ),
            )
        ]
        selected_directory_names = {directory.name for directory in selected_directories}

        embedding_similarities = self._embedding_similarities(intent, skills)

        scored: list[tuple[RoutedSkill, float, float, float, int]] = []
        for index, skill in enumerate(skills):
            routed, signal_score, specific_score, affinity_score = self._score_skill(
                skill,
                intent,
                domains,
                scopes,
                registry,
                selected_directory_names,
                embedding_similarity=embedding_similarities.get(index, 0.0),
            )
            scored.append(
                (routed, signal_score, specific_score, affinity_score, index)
            )

        # Broad intent/action labels are useful ranking context, but they do
        # not prove that a particular Skill applies. Admission requires
        # query-specific lexical/entity/semantic evidence or an explicit
        # user reference. Capability, directory, tool and source bonuses may
        # reorder admitted candidates, never create them.
        eligible = [
            item
            for item in scored
            if item[0].score > 0
            and (
                item[0].explicitly_requested
                or item[2] >= _MIN_SPECIFIC_SIGNAL
            )
        ]
        if not eligible:
            return SkillRoutingResult(
                intent_type=intent.intent_type.value,
                action_type=intent.action_type.value,
                capability_domains=[domain.value for domain in domains],
                capability_scopes=[scope.value for scope in scopes],
                selected=[],
                selected_skills=[],
                selected_directories=[],
                tool_affinity={},
                total_skills=len(skills),
                used_fallback=True,
            )

        explicit = [item for item in eligible if item[0].explicitly_requested]
        inferred = [item for item in eligible if not item[0].explicitly_requested]
        if inferred:
            strongest_specific = max(item[2] for item in inferred)
            inferred = [
                item
                for item in inferred
                if item[2]
                >= max(
                    _MIN_SPECIFIC_SIGNAL,
                    strongest_specific - _SPECIFIC_SIGNAL_MARGIN,
                )
            ]
            if strongest_specific < _WEAK_SPECIFIC_SIGNAL_THRESHOLD:
                # One weak lexical hit should offer one best suggestion, not
                # fill the whole prompt with equally tenuous candidates.
                inferred = sorted(
                    inferred,
                    key=lambda item: (
                        -item[2],
                        -item[0].score,
                        -_SOURCE_PRIORITY.get(item[0].source, -1),
                        item[0].qualified_name or item[0].name,
                        item[4],
                    ),
                )[:1]

        sorted_skills = sorted(
            [*explicit, *inferred],
            key=lambda item: (
                -int(item[0].explicitly_requested),
                -item[2],
                -item[0].score,
                -_SOURCE_PRIORITY.get(item[0].source, -1),
                item[0].qualified_name or item[0].name,
                item[4],
            ),
        )[:top_k]
        selected = [routed for routed, _, _, _, _ in sorted_skills]
        selected_skill_directories = {skill.directory for skill in selected if skill.directory}
        final_directories = [
            directory for directory in selected_directories
            if directory.name in selected_skill_directories
        ]
        return SkillRoutingResult(
            intent_type=intent.intent_type.value,
            action_type=intent.action_type.value,
            capability_domains=[domain.value for domain in domains],
            capability_scopes=[scope.value for scope in scopes],
            selected=selected,
            selected_skills=selected,
            selected_directories=final_directories,
            tool_affinity={
                skill.qualified_name or skill.name: round(affinity, 3)
                for skill, _, _, affinity, _ in sorted_skills
                if affinity != 0
            },
            total_skills=len(skills),
            used_fallback=False,
        )

    def _score_directories(
        self,
        skills: list[dict],
        intent: ParsedIntent,
        domains: list[CapabilityDomain],
        scopes: list[CapabilityScope],
    ) -> list[tuple[RoutedSkillDirectory, float]]:
        by_name: dict[str, dict[str, Any]] = {}
        for skill in skills:
            directory_name = str(skill.get("directory", "")).strip()
            if not directory_name:
                continue
            data = by_name.setdefault(
                directory_name,
                {
                    "name": directory_name,
                    "description": str(skill.get("directory_description", "")),
                    "source": str(skill.get("source", "")),
                    "domains": set(),
                    "scopes": set(),
                    "keywords": set(),
                },
            )
            if not data["description"] and skill.get("directory_description"):
                data["description"] = str(skill.get("directory_description", ""))
            if _SOURCE_PRIORITY.get(str(skill.get("source", "")), -1) > _SOURCE_PRIORITY.get(data["source"], -1):
                data["source"] = str(skill.get("source", ""))
            data["domains"].update(_list_field(skill, "domains"))
            data["scopes"].update(_list_field(skill, "scopes"))
            data["keywords"].update(_list_field(skill, "keywords"))

        scored: list[tuple[RoutedSkillDirectory, float]] = []
        for data in by_name.values():
            text = _normalize_text(
                " ".join(
                    [
                        data["name"],
                        data["description"],
                        " ".join(sorted(data["domains"])),
                        " ".join(sorted(data["scopes"])),
                        " ".join(sorted(data["keywords"])),
                    ]
                )
            )
            score, signal_score, _specific_score, reasons = _score_text(
                text, intent, domains, scopes, alias=self._alias
            )
            source_bonus = _SOURCE_BONUS.get(data["source"], 0.0)
            if source_bonus:
                score += source_bonus
                reasons.append(f"source:{data['source']}")
            scored.append((
                RoutedSkillDirectory(
                    name=data["name"],
                    description=data["description"] or data["name"],
                    source=data["source"],
                    score=round(score, 3),
                    reasons=reasons,
                ),
                signal_score,
            ))
        return scored

    def _embedding_similarities(
        self, intent: ParsedIntent, skills: list[dict]
    ) -> dict[int, float]:
        """One batched embedding pass for the whole catalog, or {} on skip."""
        if self._embedding is None or not skills:
            return {}
        query = str(getattr(intent, "raw_input", "") or "").strip()
        if not query:
            return {}
        now = time.time()
        circuit_identity = _embedding_circuit_identity(self._embedding)
        if circuit_identity is None:
            if now < self._embedding_retry_after:
                return {}
        elif not _begin_embedding_attempt(circuit_identity, now):
            return {}
        from minicode.skill_semantics import EmbeddingUnavailable

        pairs = [(_skill_embed_key(skill), _skill_match_text(skill)) for skill in skills]
        try:
            similarities = self._embedding.similarities(query, pairs)
        except EmbeddingUnavailable as error:
            if circuit_identity is None:
                self._embedding_retry_after = (
                    now + _EMBED_RETRY_COOLDOWN_SECONDS
                )
            else:
                _finish_embedding_attempt(
                    circuit_identity,
                    retry_after=now + _EMBED_RETRY_COOLDOWN_SECONDS,
                )
            logger.warning(
                "SkillRouter: embedding unavailable (%s); alias matching only "
                "for %.0fs",
                error,
                _EMBED_RETRY_COOLDOWN_SECONDS,
            )
            return {}
        except BaseException:
            if circuit_identity is not None:
                _finish_embedding_attempt(circuit_identity, retry_after=0.0)
            raise
        if circuit_identity is not None:
            _finish_embedding_attempt(circuit_identity, retry_after=0.0)
        return {
            index: similarity
            for index, similarity in enumerate(similarities)
            if similarity > 0.0
        }

    def _score_skill(
        self,
        skill: dict,
        intent: ParsedIntent,
        domains: list[CapabilityDomain],
        scopes: list[CapabilityScope],
        registry: CapabilityRegistry,
        selected_directory_names: set[str],
        *,
        embedding_similarity: float = 0.0,
    ) -> tuple[RoutedSkill, float, float, float]:
        name = str(skill.get("name", ""))
        qualified_name = str(skill.get("qualified_name") or name)
        description = str(skill.get("description", ""))
        source = str(skill.get("source", ""))
        directory = str(skill.get("directory", ""))
        tools = _list_field(skill, "tools")
        content = skill.get("content")
        supplied_digest = skill.get("content_digest")
        content_digest = (
            supplied_digest
            if isinstance(supplied_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", supplied_digest)
            else (
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                if isinstance(content, str)
                else ""
            )
        )
        text = _skill_match_text(skill)
        score, signal_score, specific_score, reasons = _score_text(
            text, intent, domains, scopes, alias=self._alias
        )

        explicitly_requested = _is_explicit_skill_reference(
            str(getattr(intent, "raw_input", "") or ""),
            name,
            qualified_name,
        )
        if explicitly_requested:
            # Exact user authority outranks every inferred semantic signal.
            score += 100.0
            signal_score += 100.0
            specific_score += 100.0
            reasons.append("explicit-skill-reference")

        if directory and directory in selected_directory_names:
            score += 1.0
            reasons.append(f"directory:{directory}")

        if embedding_similarity > 0.0:
            signal_threshold = (
                self._embed_signal_threshold_unknown
                if intent.intent_type == IntentType.UNKNOWN
                else self._embed_signal_threshold
            )
            if embedding_similarity >= signal_threshold:
                score += 1.5
                signal_score += 1.5
                specific_score += 1.5
                reasons.append(f"semantic:embedding({embedding_similarity:.2f})")
            elif embedding_similarity >= self._embed_boost_threshold:
                # Ranking help only — similarity below the signal threshold
                # can reorder candidates but never break an abstention.
                score += 0.6
                reasons.append(
                    f"semantic:embedding-boost({embedding_similarity:.2f})"
                )

        affinity_score, affinity_reasons = self._tool_affinity(skill, intent, domains, scopes, registry)
        score += affinity_score
        reasons.extend(affinity_reasons)

        source_bonus = _SOURCE_BONUS.get(source, 0.0)
        if source_bonus:
            score += source_bonus
            reasons.append(f"source:{source}")

        # Cross-Run evidence has ranking authority only. Preserve the sign of
        # the independent score so feedback cannot admit a candidate, remove
        # an otherwise eligible one, or break abstention. Exact user requests
        # remain wholly outside learned ranking.
        base_score = score
        evidence_adjustment = 0.0
        if self._routing_feedback is not None and not explicitly_requested:
            try:
                decision = self._routing_feedback.decision(
                    qualified_name=qualified_name,
                    source=source,
                    directory=directory,
                    content_digest=content_digest,
                    intent_type=intent.intent_type.value,
                    action_type=intent.action_type.value,
                )
            except Exception:  # noqa: BLE001 - evidence is optional ranking
                decision = None
            if decision is not None:
                adjustment = float(decision.adjustment)
                evidence_adjustment = adjustment
                adjusted = base_score + adjustment
                if base_score > 0:
                    score = max(0.001, adjusted)
                elif base_score < 0:
                    score = min(-0.001, adjusted)
                reasons.append(
                    f"evidence:{decision.status}({adjustment:+.3f})"
                )

        return (
            RoutedSkill(
                name=name,
                qualified_name=qualified_name,
                description=description,
                path=str(skill.get("path", "")),
                source=source,
                directory=directory,
                tools=tools,
                content_digest=content_digest,
                explicitly_requested=explicitly_requested,
                evidence_adjustment=evidence_adjustment,
                score=round(score, 3),
                reasons=reasons,
            ),
            signal_score,
            specific_score,
            affinity_score,
        )

    def _tool_affinity(
        self,
        skill: dict,
        intent: ParsedIntent,
        domains: list[CapabilityDomain],
        scopes: list[CapabilityScope],
        registry: CapabilityRegistry,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        desired_domains = set(domains)
        desired_scopes = set(scopes)
        readonly_task = intent.action_type in {ActionType.READ, ActionType.ANALYZE}

        for tool_name in _list_field(skill, "tools"):
            capability = registry.get(tool_name)
            if capability is None:
                continue
            domain = capability.metadata.domain
            scope = capability.metadata.scope
            if domain in desired_domains:
                score += 0.7
                reasons.append(f"tool-domain:{domain.value}")
            if scope in desired_scopes:
                score += 0.4
                reasons.append(f"tool-scope:{scope.value}")
            if readonly_task and scope in {CapabilityScope.DESTRUCTIVE, CapabilityScope.EXTERNAL}:
                score -= 1.0
                reasons.append(f"tool-scope-penalty:{scope.value}")
        return score, reasons

    def _relevant_capabilities(
        self,
        intent: ParsedIntent,
        registry: CapabilityRegistry,
    ) -> tuple[list[CapabilityDomain], list[CapabilityScope]]:
        available_domains: set[CapabilityDomain] = set()
        available_scopes: set[CapabilityScope] = set()
        for name in registry.list_all():
            capability = registry.get(name)
            if capability is None:
                continue
            if capability.metadata.domain != CapabilityDomain.UNKNOWN:
                available_domains.add(capability.metadata.domain)
            available_scopes.add(capability.metadata.scope)

        desired_domains = _INTENT_DOMAINS.get(intent.intent_type, set())
        domains = available_domains & desired_domains

        desired_scopes = _ACTION_SCOPES.get(intent.action_type, set())
        scopes = available_scopes & desired_scopes

        return (
            sorted(domains, key=lambda domain: domain.value),
            sorted(scopes, key=lambda scope: scope.value),
        )


def _score_text(
    text: str,
    intent: ParsedIntent,
    domains: list[CapabilityDomain],
    scopes: list[CapabilityScope],
    alias: Any = None,
) -> tuple[float, float, float, list[str]]:
    score = 0.0
    signal_score = 0.0
    specific_score = 0.0
    reasons: list[str] = []

    for term in (intent.intent_type.value, intent.action_type.value):
        if (
            term != "unknown"
            and intent.confidence >= _MIN_CONFIDENCE_INTENT_SIGNAL
            and _contains(text, term)
        ):
            score += 3.0
            signal_score += 3.0
            reasons.append(f"intent/action:{term}")

    # An UNKNOWN intent promises "no signal, abstain". Keywords and entities
    # are still extracted from the raw text in that case, so honouring them
    # here would let incidental words ("tell me a joke about python") pull
    # unrelated skills into the prompt. The one exception is the strict
    # semantic gate below: a query whose recognized concepts are *mostly*
    # covered by one skill is a real routing decision, not a coincidence.
    if intent.intent_type != IntentType.UNKNOWN:
        for keyword in intent.keywords:
            if _normalize_text(keyword) in _GENERIC_ROUTING_KEYWORDS:
                continue
            if _term_matches(text, keyword, alias):
                score += 1.0
                signal_score += 1.0
                specific_score += 1.0
                reasons.append(f"keyword:{keyword}")

        for entity in _entity_terms(intent.entities):
            if _term_matches(text, entity, alias):
                score += 1.5
                signal_score += 1.5
                specific_score += 1.5
                reasons.append(f"entity:{entity}")
    elif alias is not None:
        query_text = str(getattr(intent, "raw_input", "") or "")
        concepts = alias.matched_concepts(query_text, text)
        if len(concepts) >= _UNKNOWN_SEMANTIC_MIN_CONCEPTS:
            coverage = alias.query_coverage(query_text, text)
            if coverage >= _UNKNOWN_SEMANTIC_MIN_COVERAGE:
                contribution = 1.0 * min(len(concepts), 4)
                score += contribution
                signal_score += contribution
                specific_score += contribution
                reasons.append(
                    f"semantic:alias:{','.join(sorted(concepts)[:4])}"
                    f"@{coverage:.2f}"
                )

    for domain in domains:
        if any(_contains(text, term) for term in _DOMAIN_TERMS.get(domain, {domain.value})):
            score += 1.0
            reasons.append(f"capability-domain:{domain.value}")

    for scope in scopes:
        if any(_contains(text, term) for term in _SCOPE_TERMS.get(scope, {scope.value})):
            score += 0.5
            reasons.append(f"capability-scope:{scope.value}")

    return score, signal_score, specific_score, reasons


def _term_matches(text: str, term: str, alias: Any) -> bool:
    """Literal containment, or a cross-language concept alias hit."""
    if _contains(text, term):
        return True
    if alias is None:
        return False
    for related in alias.related_terms(term):
        if _contains(text, related):
            return True
    return False


def _is_explicit_skill_reference(
    raw_input: str,
    name: str,
    qualified_name: str,
) -> bool:
    """Return whether the user deliberately invoked this Skill.

    A bare word occurrence is only relevance evidence.  Treating it as an
    explicit invocation let a project Skill named ``test`` or ``review``
    acquire load-before-final authority from ordinary prose.  Explicit
    authority therefore requires an invocation with an explicit Skill label,
    a ``$`` sigil, or an input consisting solely of the identifier.
    """
    if not raw_input.strip():
        return False
    identifiers = dict.fromkeys(
        identifier.strip()
        for identifier in (qualified_name, name)
        if identifier and identifier.strip()
    )
    normalized_input = _normalize_text(raw_input)
    for identifier in identifiers:
        normalized_identifier = _normalize_text(identifier).strip()
        if not normalized_identifier:
            continue
        identifier_pattern = (
            rf"(?<![a-z0-9]){re.escape(normalized_identifier)}(?![a-z0-9])"
        )
        quoted_identifier = rf"`?\s*{identifier_pattern}\s*`?"
        if re.fullmatch(
            rf"\s*{identifier_pattern}\s*[.!?。！？]?\s*",
            normalized_input,
        ):
            return True
        explicit_patterns = (
            # Codex/Claude-style sigil. Backticks alone are intentionally not
            # authority: users routinely quote commands and symbols in prose.
            rf"\$\s*{identifier_pattern}",
            # English invocation grammar, including "Use the X Skill".
            rf"\b(?:use|load|invoke|apply|follow)\s+(?:the\s+)?(?:"
            rf"skill\s+(?:named\s+|called\s+)?{quoted_identifier}"
            rf"|{quoted_identifier}\s+skill)\b",
            rf"\bskill\s+(?:named\s+|called\s+){quoted_identifier}",
            # Chinese invocation grammar.  Merely discussing “技能路由” is
            # not enough; both an action verb and the 技能 label are required.
            rf"(?:使用|调用|加载|应用|按照)\s*(?:"
            rf"技能\s*(?:名为\s*)?{quoted_identifier}"
            rf"|{quoted_identifier}\s*技能)",
            # Cross-language UI copy is common in Chinese prompts.
            rf"(?:请\s*)?(?:使用|调用|加载|应用|按照)\s*"
            rf"{quoted_identifier}\s*(?:skill|技能)",
        )
        for pattern in explicit_patterns:
            for match in re.finditer(pattern, normalized_input):
                prefix = normalized_input[max(0, match.start() - 48) : match.start()]
                if re.search(
                    r"(?:do\s+not|don't|dont|never|avoid)\s+"
                    r"(?:(?:use|load|invoke|apply|follow)\s+)?$",
                    prefix,
                ) or re.search(
                    r"(?:不要|请勿|禁止|别)\s*(?:使用|调用|加载|应用|按照)?\s*$",
                    prefix,
                ):
                    continue
                return True
    return False


def _normalize_text(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ")


def _contains(text: str, term: str) -> bool:
    normalized = _normalize_text(str(term).strip())
    if not normalized:
        return False
    if normalized.isascii():
        return (
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
                text,
            )
            is not None
        )
    return normalized in text


def _as_skill_dict(skill: Any) -> dict[str, Any]:
    """Normalize a Skill catalog item to the dict shape the router scores.

    Production callers pass ``asdict(SkillSummary)``; direct callers of
    :func:`minicode.skills.discover_skills` pass dataclass instances. Accept
    both so the public API does not depend on an undocumented projection step.
    """
    if isinstance(skill, dict):
        return skill
    if is_dataclass(skill) and not isinstance(skill, type):
        return asdict(skill)
    to_dict = getattr(skill, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted
    return {}


def _list_field(mapping: dict, key: str) -> list[str]:
    value = mapping.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _entity_terms(entities: dict[str, list[str]]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for values in entities.values():
        for value in values:
            raw = str(value).lower()
            candidates = [raw]
            candidates.extend(part for part in raw.replace("\\", "/").split("/") if part)
            candidates.extend(part for part in raw.replace(".", " ").replace("_", " ").replace("-", " ").split() if part)
            for candidate in candidates:
                normalized = candidate.strip()
                if len(normalized) <= 2 or normalized in seen:
                    continue
                seen.add(normalized)
                terms.append(normalized)
    return terms
