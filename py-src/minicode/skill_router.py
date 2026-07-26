from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_parser import ActionType, IntentType, ParsedIntent


DEFAULT_SKILL_TOP_K = 5

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name or self.name,
            "description": self.description,
            "path": self.path,
            "source": self.source,
            "score": self.score,
            "reasons": list(self.reasons),
            "directory": self.directory,
            "tools": list(self.tools),
        }


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


class SkillRouter:
    def route(
        self,
        skills: list[dict],
        intent: ParsedIntent,
        registry: CapabilityRegistry,
        top_k: int = DEFAULT_SKILL_TOP_K,
    ) -> SkillRoutingResult:
        top_k = max(1, top_k)
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

        scored: list[tuple[RoutedSkill, float, float, int]] = []
        for index, skill in enumerate(skills):
            directory = str(skill.get("directory", ""))
            if selected_directory_names and directory and directory not in selected_directory_names:
                continue
            routed, signal_score, affinity_score = self._score_skill(
                skill,
                intent,
                domains,
                scopes,
                registry,
                selected_directory_names,
            )
            scored.append((routed, signal_score, affinity_score, index))

        if not any(signal_score > 0 for _, signal_score, _, _ in scored):
            selected = [
                RoutedSkill(
                    name=str(skill.get("name", "")),
                    qualified_name=str(skill.get("qualified_name") or skill.get("name", "")),
                    description=str(skill.get("description", "")),
                    path=str(skill.get("path", "")),
                    source=str(skill.get("source", "")),
                    directory=str(skill.get("directory", "")),
                    tools=_list_field(skill, "tools"),
                    score=0.0,
                    reasons=["fallback:no strong skill match"],
                )
                for skill in skills
            ]
            return SkillRoutingResult(
                intent_type=intent.intent_type.value,
                action_type=intent.action_type.value,
                capability_domains=[domain.value for domain in domains],
                capability_scopes=[scope.value for scope in scopes],
                selected=selected,
                selected_skills=selected,
                selected_directories=[],
                tool_affinity={},
                total_skills=len(skills),
                used_fallback=True,
            )

        sorted_skills = sorted(
            (item for item in scored if item[1] > 0),
            key=lambda item: (
                -item[0].score,
                -_SOURCE_PRIORITY.get(item[0].source, -1),
                item[0].qualified_name or item[0].name,
                item[3],
            ),
        )[:top_k]
        selected = [routed for routed, _, _, _ in sorted_skills]
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
                for skill, _, affinity, _ in sorted_skills
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
            score, signal_score, reasons = _score_text(text, intent, domains, scopes)
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

    def _score_skill(
        self,
        skill: dict,
        intent: ParsedIntent,
        domains: list[CapabilityDomain],
        scopes: list[CapabilityScope],
        registry: CapabilityRegistry,
        selected_directory_names: set[str],
    ) -> tuple[RoutedSkill, float, float]:
        name = str(skill.get("name", ""))
        qualified_name = str(skill.get("qualified_name") or name)
        description = str(skill.get("description", ""))
        source = str(skill.get("source", ""))
        directory = str(skill.get("directory", ""))
        tools = _list_field(skill, "tools")
        text = _normalize_text(
            " ".join([
                name,
                qualified_name,
                description,
                directory,
                " ".join(_list_field(skill, "domains")),
                " ".join(_list_field(skill, "scopes")),
                " ".join(_list_field(skill, "keywords")),
                " ".join(tools),
            ])
        )
        score, signal_score, reasons = _score_text(text, intent, domains, scopes)

        if directory and directory in selected_directory_names:
            score += 1.0
            signal_score += 1.0
            reasons.append(f"directory:{directory}")

        affinity_score, affinity_reasons = self._tool_affinity(skill, intent, domains, scopes, registry)
        score += affinity_score
        if affinity_score > 0:
            signal_score += affinity_score
        reasons.extend(affinity_reasons)

        source_bonus = _SOURCE_BONUS.get(source, 0.0)
        if source_bonus:
            score += source_bonus
            reasons.append(f"source:{source}")

        return (
            RoutedSkill(
                name=name,
                qualified_name=qualified_name,
                description=description,
                path=str(skill.get("path", "")),
                source=source,
                directory=directory,
                tools=tools,
                score=round(score, 3),
                reasons=reasons,
            ),
            signal_score,
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
            score += 0.4
            reasons.append(f"tool:{tool_name}")
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
        domains = available_domains & desired_domains if desired_domains else available_domains
        if not domains:
            domains = available_domains

        desired_scopes = _ACTION_SCOPES.get(intent.action_type, set())
        scopes = available_scopes & desired_scopes if desired_scopes else available_scopes
        if not scopes:
            scopes = available_scopes

        return (
            sorted(domains, key=lambda domain: domain.value),
            sorted(scopes, key=lambda scope: scope.value),
        )


def _score_text(
    text: str,
    intent: ParsedIntent,
    domains: list[CapabilityDomain],
    scopes: list[CapabilityScope],
) -> tuple[float, float, list[str]]:
    score = 0.0
    signal_score = 0.0
    reasons: list[str] = []

    for term in (intent.intent_type.value, intent.action_type.value):
        if term != "unknown" and _contains(text, term):
            score += 3.0
            signal_score += 3.0
            reasons.append(f"intent/action:{term}")

    for keyword in intent.keywords:
        if _contains(text, keyword):
            score += 1.0
            signal_score += 1.0
            reasons.append(f"keyword:{keyword}")

    for entity in _entity_terms(intent.entities):
        if _contains(text, entity):
            score += 1.5
            signal_score += 1.5
            reasons.append(f"entity:{entity}")

    for domain in domains:
        if any(_contains(text, term) for term in _DOMAIN_TERMS.get(domain, {domain.value})):
            score += 1.0
            signal_score += 1.0
            reasons.append(f"capability-domain:{domain.value}")

    for scope in scopes:
        if any(_contains(text, term) for term in _SCOPE_TERMS.get(scope, {scope.value})):
            score += 0.5
            signal_score += 0.5
            reasons.append(f"capability-scope:{scope.value}")

    return score, signal_score, reasons


def _normalize_text(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ")


def _contains(text: str, term: str) -> bool:
    normalized = _normalize_text(str(term).strip())
    if not normalized:
        return False
    return normalized in text


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
