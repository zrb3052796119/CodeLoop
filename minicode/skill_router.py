from __future__ import annotations

import hashlib
import inspect
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

from minicode.capability_contracts import CapabilityStatus
from minicode.capability_registry import (
    CapabilityDomain,
    CapabilityRegistry,
    CapabilityScope,
)
from minicode.intent_constraints import (
    ConstraintDiscourse,
    TestConstraintAction,
    TestConstraintScope,
    TestConstraintState,
    extract_test_constraints,
)
from minicode.intent_negation import (
    ACTION_MARKER_RE,
    FORBIDDANCE_MARKER_RE,
    effective_negation_matches,
    is_clause_negated,
)
from minicode.intent_parser import (
    ActionType,
    IntentType,
    ParsedIntent,
    _affirmed_evidence_projection,
)
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.skill_candidate_reranker import (
    SkillCandidateReranker,
    SkillRerankCandidate,
    SkillRerankDecision,
    SkillRerankRequest,
)
from minicode.tooling import (
    MAX_SKILL_LOAD_POLICY_BINDINGS,
    SkillLoadBinding,
    SkillLoadPolicy,
    skill_load_binding_from_value,
)
from minicode.turn_cancellation import TurnCancellationRequested

logger = logging.getLogger(__name__)


DEFAULT_SKILL_TOP_K = 5
_MAX_ROUTING_TOOLS = 64
_MAX_SKILL_RECALL_CANDIDATES = 16
_MIN_SKILL_RECALL_CANDIDATES = 8

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
    ActionType.COMPARE: {CapabilityScope.READONLY},
    ActionType.CREATE: {CapabilityScope.READONLY, CapabilityScope.WRITE},
    ActionType.UPDATE: {CapabilityScope.READONLY, CapabilityScope.WRITE},
    ActionType.RENAME: {
        CapabilityScope.READONLY,
        CapabilityScope.WRITE,
        CapabilityScope.DESTRUCTIVE,
    },
    ActionType.MOVE: {
        CapabilityScope.READONLY,
        CapabilityScope.WRITE,
        CapabilityScope.DESTRUCTIVE,
    },
    ActionType.MERGE: {CapabilityScope.READONLY, CapabilityScope.WRITE},
    ActionType.SPLIT: {CapabilityScope.READONLY, CapabilityScope.WRITE},
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
    {
        "agent",
        "branch",
        "project",
        "release",
        "skill",
        "skills",
        "token",
        "workflow",
    }
)

# Semantic routing thresholds. An UNKNOWN intent needs more semantic evidence
# than a classified one before the abstention promise may be broken: at least
# two distinct recognized concepts AND most of the query's concepts covered.
# Small talk carries at most one stray development concept, so it stays out.
# Embedding similarity thresholds live on the SkillRouter instance (env
# overridable, calibrated per provider in skill_semantics).
_UNKNOWN_SEMANTIC_MIN_CONCEPTS = 2
_UNKNOWN_SEMANTIC_MIN_COVERAGE = 0.6
_UNKNOWN_NON_TASK_CUES = re.compile(
    r"\b(?:joke|poem|story|fiction|roleplay|riddle)\b|"
    r"(?:笑话|段子|诗|故事|小说|角色扮演|谜语)"
)
_UNKNOWN_TASK_CUES = re.compile(
    r"\b(?:analy[sz]e|audit|automate|build|check|create|debug|design|"
    r"diagnose|explain|fix|implement|inspect|investigate|optimi[sz]e|"
    r"profile|recommend|refactor|repair|review|test|trace|write)\b|"
    r"(?:分析|审计|审查|检查|自动化|构建|创建|调试|设计|诊断|解释|"
    r"修复|实现|调查|优化|剖析|建议|重构|测试|追踪|编写)",
    re.IGNORECASE,
)
_ENGINEERING_PURPOSE_ONLY_CUES = re.compile(
    r"\b(?:api|app(?:lication)?|architecture|auth(?:entication)?|backend|"
    r"benchmark|bug|cache|class|cli|code(?:base)?|commit|config(?:uration)?|"
    r"cpu|database|dependency|deploy(?:ment)?|docker|docs?|documentation|"
    r"endpoint|error|file|fixture|"
    r"frontend|function|git|implementation|index|infrastructure|library|log|"
    r"manual|markdown|memory|migration|module|package|parser|performance|"
    r"profiling|pytest|query|readme|refactor|"
    r"release|repositor(?:y|ies)|route|router|routing|runtime|script|security|"
    r"server|skill|sql|summary|test|thread|token|tool|ui|workflow|adr|rfc)\b|"
    r"\.(?:c|cc|cpp|cs|go|h|hpp|java|js|jsx|kt|php|py|pyi|rb|rs|scala|"
    r"sql|swift|ts|tsx)\b|"
    r"(?:代码|源码|模块|函数|类|接口|后端|前端|数据库|查询|缓存|配置|依赖|"
    r"部署|发布|运维|容器|文件|文档|指南|手册|报告|交接|测试|错误|故障|"
    r"性能|调优|内存|记忆|持久化|上下文|压缩|摘要|路由|技能|线程|并发|"
    r"安全|权限|日志|脚本|仓库|提交|分支|解析|服务|组件|响应式|移动端)",
    re.IGNORECASE,
)
_NON_ENGINEERING_PURPOSE_ONLY_CUES = re.compile(
    r"\b(?:cafe|coffee|doctor|food|hotel|hospital|menu|movie|music|"
    r"nearby|pharmacy|picnic|recipe|restaurant|shop|shopping|tour|"
    r"travel|trip|weather|workout|gym|running|jogging|swimming|pace|"
    r"heart\s*rate|car|vehicle|coffee\s+machine|appliance)\b|"
    r"(?<!in-)\bflights?\b|"
    r"\b(?:hardware|grocery|retail|nearby)\s+stores?\b|"
    r"\bstores?\s+(?:nearby|hours?|locations?)\b|"
    r"(?:餐厅|饭店|咖啡馆|酒店|航班|旅行|旅游|菜谱|天气|医院|医生|药店|"
    r"电影|音乐|商店|购物|附近|野餐|健身|跑步|五公里|游泳|锻炼|运动|"
    r"心率|配速|汽车|车辆|车子|咖啡机|家电|"
    r"不是软件文档|非软件文档)",
    re.IGNORECASE,
)
_STRONG_ENGINEERING_PURPOSE_ONLY_CUES = re.compile(
    r"\b(?:api|architecture|backend|bug|class|cli|code(?:base)?|commit|"
    r"dependency|deploy(?:ment)?|docker|endpoint|error|"
    r"fixture|frontend|function|git|implementation|infrastructure|library|"
    r"migration|module|package|parser|pytest|readme|refactor|repository|router|"
    r"routing|runtime|script|server|source|sql|thread|tool|ui|workflow)\b|"
    r"\.(?:c|cc|cpp|cs|go|h|hpp|java|js|jsx|kt|php|py|pyi|rb|rs|scala|"
    r"sql|swift|ts|tsx)\b|"
    r"(?:代码|源码|模块|函数|类|接口|后端|前端|依赖|部署|容器|"
    r"错误|仓库|提交|解析器|脚本|服务|组件)",
    re.IGNORECASE,
)
_CJK_ADJACENT_ENGINEERING_TOKEN_RE = re.compile(
    r"(?<![a-z0-9_])(?:api|auth(?:entication)?|cli|code(?:base)?|commit|"
    r"config(?:uration)?|dependency|diff|docs?|documentation|git|module|"
    r"package|parser|pytest|readme|refactor|repository|route|router|runtime|"
    r"script|sql|test|tool)"
    r"(?![a-z0-9_])",
    re.IGNORECASE,
)
_EMBED_RETRY_COOLDOWN_SECONDS = 300.0
_MIN_SPECIFIC_SIGNAL = 1.0
_WEAK_SPECIFIC_SIGNAL_THRESHOLD = 2.0
_SPECIFIC_SIGNAL_MARGIN = 0.49
_MAX_EMBEDDING_CIRCUITS = 128
_OPERATION_PURPOSE_BONUS = 1.25
_PURPOSE_ONLY_TOPIC_FLOOR = 0.75
_ALIAS_ONLY_TOPIC_TERM_WEIGHT = 0.5
_SEMANTIC_TOPIC_GROUP_WEIGHT = 0.5
_MAX_SEMANTIC_TOPIC_GROUPS = 4
_PURPOSE_ONLY_FALLBACK_INTENTS = frozenset(
    {
        IntentType.CODE,
        IntentType.DOCUMENT,
        IntentType.EXPLAIN,
        IntentType.REFACTOR,
        IntentType.REVIEW,
        IntentType.SEARCH,
        IntentType.TEST,
    }
)


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
    explicitly_denied: bool = False
    evidence_adjustment: float = 0.0
    routing_identity_digest: str = ""
    operation_indexes: tuple[int, ...] = ()
    topic_specific_score: float = field(default=-1.0, repr=False)
    operation_topic_scores: tuple[tuple[int, float], ...] = field(
        default_factory=tuple,
        repr=False,
    )

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
            "routing_identity_digest": self.routing_identity_digest,
            "explicitly_requested": self.explicitly_requested,
            "operation_indexes": list(self.operation_indexes),
        }
        if self.evidence_adjustment:
            projected["evidence_adjustment"] = self.evidence_adjustment
        return projected


@dataclass(frozen=True, slots=True)
class SkillAuthorityResolution:
    """Catalog-aware current-turn Skill authority, kept out of telemetry."""

    requested: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = field(default=(), repr=False)
    ambiguous: tuple[str, ...] = field(default=(), repr=False)
    deny_all: bool = False
    context_used: bool = False


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
    routing_version: int = 3
    candidate_count: int = 0
    # The admitted pre-rerank pool is intentionally in-memory only.  It lets
    # production-shaped evaluators measure candidate recall and operation
    # association without exposing Skill identities through ``to_dict()``,
    # prompts, or telemetry events.
    inferred_candidates: tuple[RoutedSkill, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    decision_source: str = "deterministic"
    reranker_status: str = "disabled"
    decision_reason: str = ""
    authority_resolution: SkillAuthorityResolution = field(
        default_factory=SkillAuthorityResolution,
        repr=False,
    )
    turn_outcome: str = ""
    outcome_reason: str = ""
    prompt_mode: str = ""
    # Rendered character count of the model-catalog prompt section, recorded
    # by prompt assembly so catalog prompt growth stays observable.
    catalog_prompt_chars: int = 0
    # Advisory intent-derived hints for hierarchical discovery (experiment
    # arm B). None unless the intent-hints flag is on and the prompt
    # escalated to directory cards; projected by to_dict() when present.
    intent_hints: dict[str, Any] | None = None
    # Complete immutable authority snapshot for inventory fallback. It is
    # intentionally excluded from to_dict()/prompts/telemetry.
    catalog_bindings: frozenset[SkillLoadBinding] = field(
        default_factory=frozenset,
        repr=False,
    )
    # Catalog names excluded because an explicit operation prohibition
    # conflicts with a workflow's declared mandatory steps.  This stays
    # in-memory so prompt fallback and load authority cannot reintroduce a
    # candidate that deterministic admission rejected.
    constraint_vetoed_names: tuple[str, ...] = field(
        default_factory=tuple,
        repr=False,
    )

    def to_dict(self) -> dict[str, Any]:
        selected_skills = self.selected_skills or self.selected
        projected = {
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
            "routing_version": self.routing_version,
            "candidate_count": self.candidate_count,
            "decision_source": self.decision_source,
            "reranker_status": self.reranker_status,
            "decision_reason": self.decision_reason,
        }
        if self.turn_outcome:
            projected["turn_outcome"] = self.turn_outcome
        if self.outcome_reason:
            projected["outcome_reason"] = self.outcome_reason
        if self.prompt_mode:
            projected["prompt_mode"] = self.prompt_mode
        if self.catalog_prompt_chars:
            projected["catalog_prompt_chars"] = self.catalog_prompt_chars
        if self.intent_hints is not None:
            projected["intent_hints"] = dict(self.intent_hints)
        return projected

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


_ScoredSkill = tuple[RoutedSkill, float, float, float, int]


@dataclass(frozen=True, slots=True)
class _OperationSkillMatch:
    operation_index: int
    topic_score: float
    purpose_compatible: bool
    semantic_topic_groups: int = 0


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


def suggested_skill_directories_for_routing(routing: Any) -> frozenset[str]:
    """Advisory directory names from the routing result's intent hints.

    Empty unless the turn carried hierarchical hints; the discovery gate
    treats these as the routing layer's own relevance signal.
    """
    hints = None
    if isinstance(routing, dict):
        hints = routing.get("intent_hints")
    elif routing is not None:
        hints = getattr(routing, "intent_hints", None)
    if not isinstance(hints, dict):
        return frozenset()
    return frozenset(
        str(name)
        for name in (hints.get("suggested_directories") or ())
        if str(name)
    )


def skill_load_policy_for_routing(routing: Any) -> SkillLoadPolicy | None:
    """Project routing into turn-scoped, enforceable ``load_skill`` authority.

    A routed turn may load only selected Skills. Inventory fallback and
    model-first selection are bounded to the complete route-time catalog
    snapshot, minus explicit denials. Deny-all closes Skill loading for the
    entire turn in every mode; unresolved and ambiguous directives close it
    in routed modes but are advisory under model-first selection.
    """

    if routing is None:
        return None
    if isinstance(routing, dict):
        authority = routing.get("authority_resolution") or {}
        deny_all = bool(authority.get("deny_all", False))
        unresolved = tuple(authority.get("unresolved", ()) or ())
        ambiguous = tuple(authority.get("ambiguous", ()) or ())
        denied = authority.get("denied", ()) or ()
        selected = routing.get("selected_skills") or routing.get("selected") or []
        prompt_mode = str(routing.get("prompt_mode", "") or "")
        used_fallback = bool(routing.get("used_fallback", False))
        catalog_binding_values = routing.get("catalog_bindings") or ()
        constraint_vetoed = routing.get("constraint_vetoed_names") or ()
    else:
        authority = getattr(routing, "authority_resolution", None)
        deny_all = bool(getattr(authority, "deny_all", False))
        unresolved = tuple(getattr(authority, "unresolved", ()) or ())
        ambiguous = tuple(getattr(authority, "ambiguous", ()) or ())
        denied = getattr(authority, "denied", ()) or ()
        selected = (
            getattr(routing, "selected_skills", None)
            or getattr(routing, "selected", None)
            or []
        )
        prompt_mode = str(getattr(routing, "prompt_mode", "") or "")
        used_fallback = bool(getattr(routing, "used_fallback", False))
        catalog_binding_values = getattr(routing, "catalog_bindings", ()) or ()
        constraint_vetoed = (
            getattr(routing, "constraint_vetoed_names", ()) or ()
        )

    denied_names = frozenset(
        str(name).strip()
        for name in (*tuple(denied), *tuple(constraint_vetoed))
        if str(name).strip()
    )
    if len(denied_names) > MAX_SKILL_LOAD_POLICY_BINDINGS:
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            reason_code="skill-denial-policy-overflow",
        )
    # Hierarchical is the budget-escalated projection of model mode; both
    # carry the same catalog-scoped policy.
    model_catalog = prompt_mode in {"model", "hierarchical"}
    if (
        deny_all
        or (unresolved and not model_catalog)
        or (ambiguous and not model_catalog)
        or prompt_mode in {"suppressed", "constraint_conflict"}
    ):
        if prompt_mode == "constraint_conflict":
            reason = "explicit-skill-operation-conflict"
        elif deny_all or prompt_mode == "suppressed":
            reason = "user-denied-all-skills"
        elif ambiguous:
            reason = "explicit-skill-ambiguous"
        else:
            reason = "explicit-skill-unresolved"
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            denied_qualified_names=denied_names,
            reason_code=reason,
        )

    if model_catalog:
        # Model-first selection: the model chooses from the whole authorized
        # catalog, so the policy scopes to the turn's catalog snapshot minus
        # explicit denials instead of enumerating bindings; catalog size is
        # unbounded and the loader still resolves names against the snapshot.
        return SkillLoadPolicy(
            allow_catalog_scope=True,
            denied_qualified_names=denied_names,
            reason_code="model-catalog",
        )

    selected_bindings: set[SkillLoadBinding] = set()
    selected_items = selected if isinstance(selected, (list, tuple)) else ()
    for item in selected_items:
        binding = skill_load_binding_from_value(item)
        if binding is not None:
            selected_bindings.add(binding)

    if prompt_mode == "routed" or (selected_bindings and not used_fallback):
        if (
            len(selected_bindings) > MAX_SKILL_LOAD_POLICY_BINDINGS
            or len({item.qualified_name for item in selected_bindings})
            != len(selected_bindings)
        ):
            return SkillLoadPolicy(
                allowed_bindings=frozenset(),
                denied_qualified_names=denied_names,
                reason_code="skill-selection-policy-overflow",
            )
        return SkillLoadPolicy(
            allowed_bindings=frozenset(selected_bindings),
            denied_qualified_names=denied_names,
            reason_code="routed-selection",
        )
    catalog_bindings = frozenset(
        binding
        for value in catalog_binding_values
        if (binding := skill_load_binding_from_value(value)) is not None
        and binding.qualified_name not in denied_names
    )
    if (
        len(catalog_bindings) > MAX_SKILL_LOAD_POLICY_BINDINGS
        or len({item.qualified_name for item in catalog_bindings})
        != len(catalog_bindings)
    ):
        return SkillLoadPolicy(
            allowed_bindings=frozenset(),
            denied_qualified_names=denied_names,
            reason_code="skill-catalog-policy-overflow",
        )
    return SkillLoadPolicy(
        allowed_bindings=catalog_bindings,
        denied_qualified_names=denied_names,
        reason_code="inventory-fallback",
    )


def resolve_skill_authority_only(
    skills: list[object],
    intent: ParsedIntent,
) -> tuple[SkillAuthorityResolution, tuple[str, ...]]:
    """Resolve catalog Skill authority and constraint vetoes without scoring.

    Model-first selection keeps the same hard guarantees as routing —
    explicit requests/denials, deny-all, and mandatory-operation vetoes,
    which depend only on the raw input and the catalog — while skipping
    relevance scoring, embeddings, and candidate reranking entirely.
    Returns the authority resolution and the sorted constraint-vetoed
    qualified names.
    """
    normalized_skills = [_as_skill_dict(skill) for skill in skills]
    bare_name_owners: dict[str, set[str]] = {}
    for skill in normalized_skills:
        name = _normalize_authority_text(str(skill.get("name", ""))).strip()
        if not name:
            continue
        qualified_name = _normalize_authority_text(
            str(skill.get("qualified_name") or skill.get("name", ""))
        ).strip()
        if qualified_name:
            bare_name_owners.setdefault(name, set()).add(qualified_name)
    ambiguous_bare_names = {
        name for name, owners in bare_name_owners.items() if len(owners) > 1
    }
    authority_resolution = _resolve_catalog_skill_authority(
        normalized_skills,
        intent,
        ambiguous_bare_names=ambiguous_bare_names,
    )
    constraint_vetoed_names = tuple(
        sorted(
            {
                str(
                    skill.get("qualified_name") or skill.get("name") or ""
                ).strip()
                for skill in normalized_skills
                if _required_operation_denied(skill, intent)
            }
            - {""}
        )
    )
    return authority_resolution, constraint_vetoed_names


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
                " ".join(_tool_field(skill)),
            ]
        )
    )


def _skill_topic_text(skill: dict) -> str:
    """Semantic subject matter, excluding tools/capability bookkeeping."""
    return _normalize_text(
        " ".join(
            [
                str(skill.get("name", "")),
                str(skill.get("qualified_name") or skill.get("name", "")),
                str(skill.get("description", "")),
                str(skill.get("directory", "")),
                " ".join(_list_field(skill, "keywords")),
                " ".join(_list_field(skill, "examples")),
            ]
        )
    )


def _skill_identity_topic_text(skill: dict) -> str:
    """High-precision topic text from identity and summary fields only."""

    return _normalize_text(
        " ".join(
            [
                str(skill.get("name", "")),
                str(skill.get("qualified_name") or skill.get("name", "")),
                str(skill.get("description", "")),
            ]
        )
    )


def _skill_purpose_text(skill: dict) -> str:
    """Declared task purpose; tool names must never imply user intent."""
    return _normalize_text(
        " ".join(
            [
                str(skill.get("name", "")),
                str(skill.get("qualified_name") or skill.get("name", "")),
                str(skill.get("description", "")),
                str(skill.get("directory", "")),
                " ".join(_list_field(skill, "purposes")),
                " ".join(_list_field(skill, "supported_operations")),
            ]
        )
    )


_GENERAL_REVIEW_PURPOSES = frozenset(
    {
        "architecture review",
        "code audit",
        "code quality review",
        "code review",
        "design review",
        "general code audit",
        "general review",
    }
)


def _declares_general_review_purpose(skill: dict) -> bool:
    """Whether explicit purpose metadata offers a general review workflow."""
    purposes = {
        _normalize_text(value).strip()
        for value in _list_field(skill, "purposes")
    }
    return bool(purposes & _GENERAL_REVIEW_PURPOSES)


def _skill_embed_key(skill: dict) -> str:
    for field_name in ("routing_identity_digest", "content_digest"):
        digest = skill.get(field_name)
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    content = skill.get("content")
    if isinstance(content, str) and content:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return hashlib.sha256(_skill_match_text(skill).encode("utf-8")).hexdigest()


def _accepts_keyword_argument(function: Any, name: str) -> bool:
    """Inspect a callable without using TypeError as signature control flow."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return False
    parameter = signature.parameters.get(name)
    if parameter is not None and parameter.kind in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }:
        return True
    return any(
        item.kind == inspect.Parameter.VAR_KEYWORD
        for item in signature.parameters.values()
    )


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
        semantic_reranker: SkillCandidateReranker | None = None,
    ) -> None:
        from minicode.skill_semantics import (
            AliasSemanticMatcher,
            EmbeddingSemanticMatcher,
            embedding_thresholds,
        )

        self._alias = AliasSemanticMatcher()
        self._routing_feedback = routing_feedback
        self._semantic_reranker = semantic_reranker
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
        *,
        call_context: dict[str, Any] | None = None,
    ) -> SkillRoutingResult:
        top_k = max(1, top_k)
        # Accept both the production dict projection and the public
        # discover_skills() dataclass shape at this boundary.
        skills = [_as_skill_dict(skill) for skill in skills]
        catalog_bindings = frozenset(
            binding
            for skill in skills
            if (binding := skill_load_binding_from_value(skill)) is not None
        )
        bare_name_owners: dict[str, set[str]] = {}
        for skill in skills:
            name = _normalize_authority_text(str(skill.get("name", ""))).strip()
            if not name:
                continue
            qualified_name = _normalize_authority_text(
                str(skill.get("qualified_name") or skill.get("name", ""))
            ).strip()
            if qualified_name:
                bare_name_owners.setdefault(name, set()).add(qualified_name)
        ambiguous_bare_names = {
            name for name, owners in bare_name_owners.items() if len(owners) > 1
        }
        authority_resolution = _resolve_catalog_skill_authority(
            skills,
            intent,
            ambiguous_bare_names=ambiguous_bare_names,
        )
        effective_intent_type, effective_action_type = (
            _effective_routing_pair(intent)
        )
        # Mandatory workflow prohibitions are load-policy constraints even
        # when the turn contains no affirmative operation. Compute them before
        # any parser-abstention return so inventory fallback cannot reintroduce
        # a workflow the user just ruled out.
        constraint_vetoed_indexes = {
            index
            for index, skill in enumerate(skills)
            if _required_operation_denied(skill, intent)
        }
        constraint_vetoed_names = tuple(
            sorted(
                {
                    str(
                        skills[index].get("qualified_name")
                        or skills[index].get("name")
                        or ""
                    ).strip()
                    for index in constraint_vetoed_indexes
                }
                - {""}
            )
        )
        if authority_resolution.deny_all:
            return SkillRoutingResult(
                intent_type=effective_intent_type.value,
                action_type=effective_action_type.value,
                capability_domains=[],
                capability_scopes=[],
                selected=[],
                selected_skills=[],
                selected_directories=[],
                tool_affinity={},
                total_skills=len(skills),
                used_fallback=False,
                decision_reason="explicit-skill-deny-all",
                authority_resolution=authority_resolution,
                catalog_bindings=catalog_bindings,
                constraint_vetoed_names=constraint_vetoed_names,
            )
        if authority_resolution.unresolved or authority_resolution.ambiguous:
            reason = (
                "explicit-skill-ambiguous"
                if authority_resolution.ambiguous
                else "explicit-skill-unresolved"
            )
            return SkillRoutingResult(
                intent_type=effective_intent_type.value,
                action_type=effective_action_type.value,
                capability_domains=[],
                capability_scopes=[],
                selected=[],
                selected_skills=[],
                selected_directories=[],
                tool_affinity={},
                total_skills=len(skills),
                used_fallback=True,
                decision_reason=reason,
                authority_resolution=authority_resolution,
                catalog_bindings=catalog_bindings,
                constraint_vetoed_names=constraint_vetoed_names,
            )
        inferred_routing_allowed = _allows_inferred_skill_routing(intent)
        if not inferred_routing_allowed and not authority_resolution.requested:
            # A failed/abstained parser result must not be silently upgraded
            # into a Skill decision from raw keywords. Explicit Skill
            # authority is handled independently and remains available.
            return SkillRoutingResult(
                intent_type=effective_intent_type.value,
                action_type=effective_action_type.value,
                capability_domains=[],
                capability_scopes=[],
                selected=[],
                selected_skills=[],
                selected_directories=[],
                tool_affinity={},
                total_skills=len(skills),
                used_fallback=True,
                decision_reason="no-affirmed-operation",
                authority_resolution=authority_resolution,
                catalog_bindings=catalog_bindings,
                constraint_vetoed_names=constraint_vetoed_names,
            )
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

        embedding_similarities = (
            self._embedding_similarities(
                intent,
                skills,
                call_context=call_context,
                denied_skill_names=set(authority_resolution.denied),
            )
            if inferred_routing_allowed
            else {}
        )

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
                allow_bare_reference=(
                    _normalize_authority_text(str(skill.get("name", ""))).strip()
                    not in ambiguous_bare_names
                ),
                authority_resolution=authority_resolution,
            )
            scored.append(
                (routed, signal_score, specific_score, affinity_score, index)
            )

        # A workflow contract is stronger than lexical or semantic relevance.
        # If the user explicitly prohibited a step that the Skill necessarily
        # performs, the candidate must not be admitted -- even when it was
        # named explicitly.  Keep the names on the in-memory routing result so
        # inventory fallback and ``load_skill`` cannot reintroduce it later.
        # Apply score-independent gates before candidates compete for an
        # operation or its purpose-only fallback. Retain the full catalog's
        # topic evidence so an unavailable specialist does not authorize an
        # unrelated specialty through a broad operation label.
        eligible_catalog_indexes = {
            item[4]
            for item in scored
            if not item[0].explicitly_denied
            and item[4] not in constraint_vetoed_indexes
            and (
                item[0].explicitly_requested
                or (
                    inferred_routing_allowed
                    and _skill_activation_satisfied(
                        skills[item[4]],
                        intent,
                    )
                    and not _negated_skill_veto(
                        _skill_topic_text(skills[item[4]]), intent
                    )
                )
            )
        }

        scored = _apply_operation_topic_purpose_scores(
            scored,
            skills,
            intent,
            alias=self._alias,
            eligible_catalog_indexes=eligible_catalog_indexes,
        )

        # Broad intent/action labels are useful ranking context, but they do
        # not prove that a particular Skill applies. Admission requires
        # query-specific lexical/entity/semantic evidence or an explicit
        # user reference. Capability, directory, tool and source bonuses may
        # reorder admitted candidates, never create them.
        eligible = [
            item
            for item in scored
            if item[4] in eligible_catalog_indexes
            and (
                item[0].explicitly_requested
                or (item[0].score > 0 and item[2] >= _MIN_SPECIFIC_SIGNAL)
            )
        ]
        if not eligible:
            return SkillRoutingResult(
                intent_type=effective_intent_type.value,
                action_type=effective_action_type.value,
                capability_domains=[domain.value for domain in domains],
                capability_scopes=[scope.value for scope in scopes],
                selected=[],
                selected_skills=[],
                selected_directories=[],
                tool_affinity={},
                total_skills=len(skills),
                used_fallback=True,
                authority_resolution=authority_resolution,
                catalog_bindings=catalog_bindings,
                constraint_vetoed_names=constraint_vetoed_names,
            )

        def _ranking_key(
            item: _ScoredSkill,
        ) -> tuple[float, float, int, str, int]:
            return (
                -item[2],
                -item[0].score,
                -_SOURCE_PRIORITY.get(item[0].source, -1),
                item[0].qualified_name or item[0].name,
                item[4],
            )

        explicit = sorted(
            (item for item in eligible if item[0].explicitly_requested),
            key=_ranking_key,
        )
        inferred = sorted(
            (item for item in eligible if not item[0].explicitly_requested),
            key=_ranking_key,
        )

        # Preserve the legacy admission/ranking result for scalar and
        # single-operation requests. Compound requests instead keep a small
        # candidate lane per affirmed operation so a very strong first clause
        # cannot crowd every later clause out of the recall set.
        affirmed_operation_count = len(_affirmed_operation_items(intent))
        if inferred and affirmed_operation_count <= 1:
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
            strongest_topic_specific = max(
                item[0].topic_specific_score
                if item[0].topic_specific_score >= 0.0
                else item[2]
                for item in inferred
            )
            if strongest_topic_specific < _WEAK_SPECIFIC_SIGNAL_THRESHOLD:
                # One weak lexical hit should offer one best suggestion, not
                # fill the whole prompt with equally tenuous candidates.
                inferred = inferred[:1]

        for item in explicit:
            declared_operations = _declared_supported_operations(
                skills[item[4]]
            )
            item[0].operation_indexes = _candidate_operation_indexes(
                _skill_topic_text(skills[item[4]]),
                intent,
                alias=self._alias,
                supported_operations=declared_operations,
            )
            if declared_operations is not None and any(
                (operation.intent_type, operation.action_type)
                not in declared_operations
                for _index, operation in _affirmed_operation_items(intent)
            ):
                item[0].reasons.append("explicit-skill-contract-mismatch")

        recall_limit = min(
            _MAX_SKILL_RECALL_CANDIDATES,
            max(_MIN_SKILL_RECALL_CANDIDATES, top_k),
        )
        candidate_pool = _round_robin_operation_candidates(
            _prune_deterministic_operation_selection(
                inferred,
                explicit=explicit,
                intent=intent,
                skills=skills,
            ),
            skills,
            intent,
            alias=self._alias,
        )[:recall_limit]
        (
            selected_inferred,
            reranker_status,
            decision_source,
            decision_reason,
        ) = self._rerank_candidates(
            candidate_pool,
            skills,
            intent,
            top_k=top_k,
            call_context=call_context,
        )
        if decision_source == "deterministic":
            selected_inferred = _prune_deterministic_operation_selection(
                selected_inferred,
                explicit=explicit,
                intent=intent,
                skills=skills,
            )

        # Explicit authority is not a recommendation quota: every
        # unambiguous user invocation remains selected. ``top_k`` bounds only
        # inferred prompt suggestions.
        sorted_skills = [
            *explicit,
            *selected_inferred[:top_k],
        ]
        selected = [routed for routed, _, _, _, _ in sorted_skills]
        selected_skill_directories = {skill.directory for skill in selected if skill.directory}
        final_directories = [
            directory for directory in selected_directories
            if directory.name in selected_skill_directories
        ]
        return SkillRoutingResult(
            intent_type=effective_intent_type.value,
            action_type=effective_action_type.value,
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
            used_fallback=not selected,
            candidate_count=len(candidate_pool),
            inferred_candidates=tuple(item[0] for item in candidate_pool),
            decision_source=decision_source,
            reranker_status=reranker_status,
            decision_reason=decision_reason,
            authority_resolution=authority_resolution,
            catalog_bindings=catalog_bindings,
            constraint_vetoed_names=constraint_vetoed_names,
        )

    def _rerank_candidates(
        self,
        candidate_pool: list[_ScoredSkill],
        skills: list[dict],
        intent: ParsedIntent,
        *,
        top_k: int,
        call_context: dict[str, Any] | None,
    ) -> tuple[list[_ScoredSkill], str, str, str]:
        """Apply an advisory reranker without expanding its authority.

        The candidate set has already passed deterministic admission and
        negation checks. Only an accepted ordered subset or an explicit
        abstention may change it; malformed responses and provider failures
        retain the deterministic order.
        """
        reranker: Any = self._semantic_reranker
        if reranker is None and isinstance(call_context, dict):
            reranker = call_context.get("skill_candidate_reranker")
        if reranker is None:
            return (
                candidate_pool,
                "disabled",
                "deterministic",
                "deterministic-ranking",
            )
        try:
            rerank = getattr(reranker, "rerank", None)
        except Exception:  # noqa: BLE001 - untrusted adapter fails closed
            rerank = None
        if not callable(rerank):
            return (
                candidate_pool,
                "invalid_adapter",
                "deterministic",
                "reranker-adapter-invalid",
            )
        if not candidate_pool:
            return (
                candidate_pool,
                "no_candidates",
                "deterministic",
                "no-inferred-candidates",
            )

        by_candidate_id: dict[str, _ScoredSkill] = {}
        candidates: list[SkillRerankCandidate] = []
        try:
            for item in candidate_pool:
                routed = item[0]
                candidate_id = _opaque_candidate_id(routed, item[4])
                if candidate_id in by_candidate_id:
                    return (
                        candidate_pool,
                        "invalid_request",
                        "deterministic",
                        "candidate-id-collision",
                    )
                by_candidate_id[candidate_id] = item
                candidates.append(
                    SkillRerankCandidate(
                        candidate_id=candidate_id,
                        description=_rerank_candidate_description(
                            skills[item[4]]
                        ),
                        retrieval_score=float(routed.score),
                        operation_indexes=routed.operation_indexes,
                    )
                )
            routing_intent_type, routing_action_type = _effective_routing_pair(
                intent
            )
            request = SkillRerankRequest(
                query=_affirmed_routing_query(intent),
                intent_type=routing_intent_type.value,
                action_type=routing_action_type.value,
                candidates=tuple(candidates),
                top_k=min(top_k, len(candidates)),
            )
            decision = rerank(
                request,
                call_context=call_context,
            )
        except (TurnCancellationRequested, ModelCallDeadlineExceeded):
            # Parent Turn control flow must never be converted into a normal
            # routing fallback.
            raise
        except Exception as error:  # noqa: BLE001 - advisory seam fails closed
            logger.warning(
                "SkillRouter: candidate reranker failed (%s); using "
                "deterministic order",
                type(error).__name__,
            )
            return (
                candidate_pool,
                "error",
                "deterministic",
                f"reranker-error:{type(error).__name__}",
            )

        if not isinstance(decision, SkillRerankDecision):
            return (
                candidate_pool,
                "invalid_response",
                "deterministic",
                "reranker-decision-type-invalid",
            )

        status = decision.status
        ordered_ids = tuple(decision.ordered_candidate_ids)
        reason = _reranker_decision_reason(decision)
        if status == "abstained":
            if ordered_ids:
                return (
                    candidate_pool,
                    "invalid_response",
                    "deterministic",
                    "abstention-selected-candidates",
                )
            return [], status, "semantic_reranker", reason

        if status == "accepted":
            if (
                not ordered_ids
                or len(ordered_ids) > request.top_k
                or len(set(ordered_ids)) != len(ordered_ids)
                or any(candidate_id not in by_candidate_id for candidate_id in ordered_ids)
            ):
                return (
                    candidate_pool,
                    "invalid_response",
                    "deterministic",
                    "reranker-candidate-authority-violation",
                )
            return (
                [by_candidate_id[candidate_id] for candidate_id in ordered_ids],
                status,
                "semantic_reranker",
                reason,
            )

        # Budget, timeout, low-confidence, disabled and all other fail-closed
        # statuses are diagnostics only; they have no ranking authority.
        if ordered_ids:
            status = "invalid_response"
            reason = "non-accepted-decision-selected-candidates"
        return candidate_pool, status, "deterministic", reason

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
        self,
        intent: ParsedIntent,
        skills: list[dict],
        *,
        call_context: dict[str, Any] | None = None,
        denied_skill_names: set[str] | None = None,
    ) -> dict[int, float]:
        """One batched embedding pass for the whole catalog, or {} on skip."""
        if self._embedding is None or not skills:
            return {}
        query = _affirmed_routing_query(intent)
        if not query:
            return {}
        denied = denied_skill_names or set()
        eligible_skills = [
            (index, skill)
            for index, skill in enumerate(skills)
            if str(skill.get("qualified_name") or skill.get("name", ""))
            not in denied
        ]
        if not eligible_skills:
            return {}
        now = time.time()
        circuit_identity = _embedding_circuit_identity(self._embedding)
        if circuit_identity is None:
            if now < self._embedding_retry_after:
                return {}
        elif not _begin_embedding_attempt(circuit_identity, now):
            return {}
        from minicode.skill_semantics import EmbeddingUnavailable

        pairs = [
            (_skill_embed_key(skill), _skill_match_text(skill))
            for _index, skill in eligible_skills
        ]
        try:
            matcher = self._embedding.similarities
            if _accepts_keyword_argument(matcher, "call_context"):
                similarities = matcher(
                    query,
                    pairs,
                    call_context=call_context,
                )
            else:
                similarities = matcher(query, pairs)
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
            catalog_index: similarity
            for (catalog_index, _skill), similarity in zip(
                eligible_skills,
                similarities,
            )
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
        allow_bare_reference: bool = True,
        authority_resolution: SkillAuthorityResolution | None = None,
    ) -> tuple[RoutedSkill, float, float, float]:
        name = str(skill.get("name", ""))
        qualified_name = str(skill.get("qualified_name") or name)
        description = str(skill.get("description", ""))
        source = str(skill.get("source", ""))
        directory = str(skill.get("directory", ""))
        tools = _tool_field(skill)
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
        supplied_routing_digest = skill.get("routing_identity_digest")
        routing_identity_digest = (
            supplied_routing_digest
            if isinstance(supplied_routing_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", supplied_routing_digest)
            else content_digest
        )
        text = _skill_topic_text(skill)
        score, signal_score, specific_score, reasons = _score_text(
            text, intent, domains, scopes, alias=self._alias
        )

        canonical_identity = qualified_name or name
        if authority_resolution is None:
            explicit_directive = _resolved_skill_directive(
                intent,
                str(getattr(intent, "raw_input", "") or ""),
                name,
                qualified_name,
                allow_bare_reference=allow_bare_reference,
            )
        elif canonical_identity in authority_resolution.requested:
            explicit_directive = "requested"
        elif canonical_identity in authority_resolution.denied:
            explicit_directive = "denied"
        else:
            explicit_directive = "none"
        explicitly_requested = explicit_directive == "requested"
        explicitly_denied = explicit_directive == "denied"
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
            routing_intent_type, _routing_action_type = _effective_routing_pair(
                intent
            )
            signal_threshold = (
                self._embed_signal_threshold_unknown
                if routing_intent_type == IntentType.UNKNOWN
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
                feedback_decision = self._routing_feedback.decision
                routing_intent_type, routing_action_type = (
                    _effective_routing_pair(intent)
                )
                feedback_query: dict[str, Any] = {
                    "qualified_name": qualified_name,
                    "source": source,
                    "directory": directory,
                    "content_digest": content_digest,
                    "intent_type": routing_intent_type.value,
                    "action_type": routing_action_type.value,
                }
                if _accepts_keyword_argument(
                    feedback_decision,
                    "routing_identity_digest",
                ):
                    feedback_query["routing_identity_digest"] = (
                        routing_identity_digest
                    )
                else:
                    # Legacy feedback providers use content_digest as their
                    # only version key. Give them the stronger identity so
                    # stale routing metadata cannot inherit old authority.
                    feedback_query["content_digest"] = routing_identity_digest
                decision = feedback_decision(
                    **feedback_query,
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
                routing_identity_digest=routing_identity_digest,
                explicitly_requested=explicitly_requested,
                explicitly_denied=explicitly_denied,
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
        readonly_task = _effective_routing_is_read_only(intent)

        for tool_name in _tool_field(skill):
            capability = registry.get(tool_name)
            if capability is None:
                continue
            if capability.metadata.profile.status is not CapabilityStatus.DECLARED:
                # Keep guesses visible without scoring them as verified effects.
                reasons.append("tool-capability-unverified")
                continue
            domain = capability.metadata.domain
            scope = capability.metadata.scope
            if domain in desired_domains:
                score += 0.7
                reasons.append(f"tool-domain:{domain.value}")
            if scope in desired_scopes:
                score += 0.4
                reasons.append(f"tool-scope:{scope.value}")
            effects = capability.metadata.profile.effects
            if readonly_task and (
                effects.destructive is True or effects.read_only is False
            ):
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
            if capability.metadata.profile.status is not CapabilityStatus.DECLARED:
                continue
            if capability.metadata.domain != CapabilityDomain.UNKNOWN:
                available_domains.add(capability.metadata.domain)
            available_scopes.add(capability.metadata.scope)

        operations = tuple(getattr(intent, "operations", ()) or ())
        affirmed_operations = tuple(
            operation for _index, operation in _affirmed_operation_items(intent)
        )
        intent_types = (
            {operation.intent_type for operation in affirmed_operations}
            if affirmed_operations
            else ({intent.intent_type} if not operations else set())
        )
        desired_domains: set[CapabilityDomain] = set()
        for intent_type in intent_types:
            desired_domains.update(_INTENT_DOMAINS.get(intent_type, set()))
        domains = available_domains & desired_domains

        action_types = (
            {operation.action_type for operation in affirmed_operations}
            if affirmed_operations
            else ({intent.action_type} if not operations else set())
        )
        desired_scopes: set[CapabilityScope] = set()
        for action_type in action_types:
            desired_scopes.update(_ACTION_SCOPES.get(action_type, set()))
        scopes = available_scopes & desired_scopes

        return (
            sorted(domains, key=lambda domain: domain.value),
            sorted(scopes, key=lambda scope: scope.value),
        )


_OPERATION_ROUTING_TERMS: dict[tuple[IntentType, ActionType], set[str]] = {
    # ``pytest`` is a framework/topic discriminator, not a generic execution
    # purpose. Keeping it out of this set lets pytest-specific workflows beat
    # a generic regression-test workflow for a terse "run pytest" request.
    (IntentType.TEST, ActionType.EXECUTE): {
        "execute",
        "run",
        "test",
        "testing",
        "tests",
        "verification",
        "verify",
    },
    (IntentType.CODE, ActionType.DELETE): {"delete", "remove", "deletion"},
    (IntentType.CODE, ActionType.UPDATE): {
        "design",
        "edit",
        "fix",
        "modify",
        "redesign",
        "repair",
        "update",
    },
    (IntentType.CODE, ActionType.CREATE): {
        "build",
        "create",
        "design",
        "implement",
    },
    (IntentType.CODE, ActionType.RENAME): {"rename", "renaming"},
    (IntentType.DEBUG, ActionType.ANALYZE): {
        "debug",
        "diagnose",
        "failure",
        "profile",
        "root cause",
        "slow",
        "troubleshoot",
    },
    (IntentType.DEBUG, ActionType.UPDATE): {
        "debug",
        "fix",
        "repair",
        "troubleshoot",
    },
    (IntentType.REFACTOR, ActionType.UPDATE): {
        "optimize",
        "performance",
        "refactor",
        "tune",
        "tuning",
    },
    (IntentType.EXPLAIN, ActionType.READ): {
        "describe",
        "explain",
        "explanation",
        "understand",
        "walkthrough",
    },
    (IntentType.REVIEW, ActionType.ANALYZE): {
        "analysis",
        "analyze",
        "audit",
        "inspect",
        "regression",
        "regressions",
        "review",
    },
    (IntentType.SEARCH, ActionType.READ): {
        "definition",
        "find",
        "locate",
        "reference",
        "references",
        "search",
    },
    (IntentType.DOCUMENT, ActionType.CREATE): {
        "docs",
        "documentation",
        "document",
        "guide",
        "manual",
        "readme",
        "write",
    },
    (IntentType.DOCUMENT, ActionType.UPDATE): {
        "docs",
        "documentation",
        "document",
        "guide",
        "manual",
        "readme",
        "revise",
    },
    (IntentType.CONFIGURE, ActionType.CREATE): {
        "automate",
        "configure",
    },
    (IntentType.CONFIGURE, ActionType.UPDATE): {
        "automate",
        "configure",
        "manage",
    },
}


def _operation_terms(intent_type: IntentType, action_type: ActionType) -> set[str]:
    terms = set(_OPERATION_ROUTING_TERMS.get((intent_type, action_type), set()))
    if intent_type is not IntentType.UNKNOWN:
        terms.add(intent_type.value)
    if action_type is not ActionType.UNKNOWN:
        terms.add(action_type.value)
    return terms


def _operation_purpose_terms(
    intent_type: IntentType,
    action_type: ActionType,
) -> set[str]:
    """Purpose cues without generic read/update/create action pollution."""
    return set(
        _OPERATION_ROUTING_TERMS.get((intent_type, action_type), set())
    )


def _declared_supported_operations(
    skill: dict,
) -> frozenset[tuple[IntentType, ActionType]] | None:
    """Parse a Skill's explicit operation contract.

    A non-empty ``supported_operations`` list is authoritative.  Catalogs
    created before this field existed retain the legacy prose fallback by
    omitting it (or projecting an empty list).  Malformed declarations fail
    closed instead of accidentally widening a Skill through description text.
    """
    values = _list_field(skill, "supported_operations")
    if not values:
        return None

    operations: set[tuple[IntentType, ActionType]] = set()
    for value in values:
        parts = value.strip().lower().split(".")
        if len(parts) != 2:
            return frozenset()
        try:
            intent_type = IntentType(parts[0])
            action_type = ActionType(parts[1])
        except ValueError:
            return frozenset()
        if (
            intent_type is IntentType.UNKNOWN
            or action_type is ActionType.UNKNOWN
        ):
            return frozenset()
        operations.add((intent_type, action_type))
    return frozenset(operations)


def _declared_required_operations(
    skill: dict,
) -> frozenset[tuple[IntentType, ActionType]] | None:
    """Parse mandatory workflow steps; malformed declarations fail closed.

    ``supported_operations`` says which request lanes a Skill can serve.
    ``required_operations`` instead says which operations the workflow will
    necessarily perform once selected.  An empty declaration means that the
    Skill has no universal mandatory step; ``None`` marks malformed metadata.
    """

    values = _list_field(skill, "required_operations")
    if not values:
        return frozenset()
    operations: set[tuple[IntentType, ActionType]] = set()
    for value in values:
        parts = value.strip().lower().split(".")
        if len(parts) != 2:
            return None
        try:
            intent_type = IntentType(parts[0])
            action_type = ActionType(parts[1])
        except ValueError:
            return None
        if (
            intent_type is IntentType.UNKNOWN
            or action_type is ActionType.UNKNOWN
        ):
            return None
        operations.add((intent_type, action_type))
    return frozenset(operations)


_REQUIRED_OPERATION_SUBTYPE_RE = re.compile(
    r"\b(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|regression|security|slow|smoke|system|unit)\b|"
    r"(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|慢速|耗时|"
    r"冒烟|回归|系统|单元)",
    re.IGNORECASE,
)
_REQUIRED_OPERATION_SUBTYPE_ALIASES = (
    frozenset({"acceptance", "验收"}),
    frozenset({"browser", "浏览器"}),
    frozenset({"contract", "契约"}),
    frozenset({"e2e", "end-to-end", "end to end", "端到端"}),
    frozenset({"integration", "集成"}),
    frozenset({"load", "负载"}),
    frozenset({"performance", "性能"}),
    frozenset({"regression", "回归"}),
    frozenset({"security", "安全"}),
    frozenset({"slow", "慢速", "耗时"}),
    frozenset({"smoke", "冒烟"}),
    frozenset({"system", "系统"}),
    frozenset({"unit", "单元"}),
)


def _skill_topic_matches_test_subtype(topic_text: str, subtype: str) -> bool:
    normalized = subtype.strip().casefold()
    aliases = next(
        (
            group
            for group in _REQUIRED_OPERATION_SUBTYPE_ALIASES
            if normalized in group
        ),
        frozenset({normalized}),
    )
    return any(_contains(topic_text, alias) for alias in aliases)


def _required_operation_denied(skill: dict, intent: ParsedIntent) -> bool:
    """Hard-veto a workflow whose mandatory operation was prohibited.

    A scoped denial such as ``do not run integration tests`` does not block a
    regression-test workflow unless that subtype is part of the Skill topic.
    Unqualified denials (``skip tests``) and matching subtype denials do.
    """

    required = _declared_required_operations(skill)
    if required is None:
        return True
    if not required:
        return False
    topic_text = _skill_topic_text(skill)
    raw_input = str(getattr(intent, "raw_input", "") or "")
    constraints = extract_test_constraints(raw_input) if raw_input.strip() else ()
    constraint_action_by_required_action = {
        ActionType.CREATE: TestConstraintAction.CREATE,
        ActionType.UPDATE: TestConstraintAction.CREATE,
        ActionType.EXECUTE: TestConstraintAction.EXECUTE,
    }
    typed_test_pairs: set[tuple[IntentType, ActionType]] = set()
    for intent_type, action_type in required:
        if intent_type is not IntentType.TEST:
            continue
        constraint_action = constraint_action_by_required_action.get(action_type)
        if constraint_action is None:
            continue
        typed_test_pairs.add((intent_type, action_type))
        if any(
            constraint.action is constraint_action
            and constraint.state is TestConstraintState.DENIED
            and constraint.discourse is ConstraintDiscourse.DIRECTIVE
            and (
                constraint.scope is TestConstraintScope.GLOBAL
                or any(
                    _skill_topic_matches_test_subtype(topic_text, subtype)
                    for subtype in constraint.subtypes
                )
            )
            for constraint in constraints
        ):
            return True

    for operation in tuple(getattr(intent, "negated_operations", ()) or ()):
        pair = (
            getattr(operation, "intent_type", IntentType.UNKNOWN),
            getattr(operation, "action_type", ActionType.UNKNOWN),
        )
        if pair not in required:
            continue
        # With current-turn source text, typed constraints are authoritative
        # for mandatory test creation/execution.  Falling back to a broad
        # parser negative here would reintroduce status, quotation, and
        # double-negation false vetoes.  The operation fallback remains for
        # legacy/manual ParsedIntent values that carry no raw input.
        if raw_input.strip() and pair in typed_test_pairs:
            continue
        if pair[0] is not IntentType.TEST:
            return True
        clause = str(getattr(operation, "clause", "") or "")
        subtypes = tuple(
            match.group(0)
            for match in _REQUIRED_OPERATION_SUBTYPE_RE.finditer(clause)
        )
        if not subtypes or any(
            _skill_topic_matches_test_subtype(topic_text, item)
            for item in subtypes
        ):
            return True
    return False


def _affirmed_routing_query(intent: ParsedIntent) -> str:
    """Return routing evidence from affirmed clauses plus safe context topics.

    The legacy scalar shape has no operations, so it retains its raw query.
    For V2 intents, negated clauses are deliberately omitted from lexical and
    embedding evidence. Explicit Skill authority is parsed separately from the
    current raw input and never comes through this projection.
    """
    operations = tuple(getattr(intent, "operations", ()) or ())
    if not operations:
        raw_input = str(getattr(intent, "raw_input", "") or "").strip()
        if raw_input and is_clause_negated(raw_input):
            return ""
        return str(
            getattr(intent, "semantic_query", "") or raw_input
        ).strip()

    clauses = [
        " ".join(
            _affirmed_evidence_projection(str(operation.clause)).split()
        )
        for _index, operation in _affirmed_operation_items(intent)
        if _affirmed_evidence_projection(str(operation.clause)).strip()
    ]
    if not clauses:
        return ""
    query = " ".join(clauses)
    semantic_query = str(getattr(intent, "semantic_query", "") or "")
    context_marker = "\nContext topics:"
    if context_marker in semantic_query:
        context_topics = semantic_query.split(context_marker, 1)[1].strip()
        if context_topics:
            query = f"{query}{context_marker} {context_topics}"
    return query


def _affirmed_routing_entities(intent: ParsedIntent) -> dict[str, list[str]]:
    operations = tuple(getattr(intent, "operations", ()) or ())
    if not operations:
        return {
            str(key): [str(value) for value in values]
            for key, values in getattr(intent, "entities", {}).items()
        }
    entities: dict[str, list[str]] = {}
    for _index, operation in _affirmed_operation_items(intent):
        raw_clause = str(getattr(operation, "clause", "") or "")
        projected_clause = _affirmed_evidence_projection(raw_clause)
        for key, values in operation.entities.items():
            bucket = entities.setdefault(str(key), [])
            for value in values:
                normalized = str(value)
                if (
                    normalized
                    and _contains(raw_clause, normalized)
                    and not _contains(projected_clause, normalized)
                ):
                    continue
                if normalized and normalized not in bucket:
                    bucket.append(normalized)
    return entities


def _affirmed_routing_keywords(intent: ParsedIntent) -> list[str]:
    operations = tuple(getattr(intent, "operations", ()) or ())
    if not operations:
        return [str(keyword) for keyword in getattr(intent, "keywords", ())]
    keywords: list[str] = []
    for _index, operation in _affirmed_operation_items(intent):
        raw_clause = str(getattr(operation, "clause", "") or "")
        projected_clause = _affirmed_evidence_projection(raw_clause)
        for keyword in getattr(operation, "keywords", ()):
            normalized = str(keyword)
            if (
                normalized
                and _contains(raw_clause, normalized)
                and not _contains(projected_clause, normalized)
            ):
                continue
            if normalized and normalized not in keywords:
                keywords.append(normalized)
    return keywords


def _negated_skill_veto(text: str, intent: ParsedIntent) -> bool:
    """Prevent a negative clause from independently admitting a Skill.

    An explicitly requested Skill is handled before this veto. A mixed Skill
    remains eligible when its text also matches an affirmed operation, so
    "review, don't fix" can still select a review Skill that mentions fixes.
    """
    negated = tuple(getattr(intent, "negated_operations", ()) or ())
    if not negated:
        return False
    affirmed = tuple(
        operation for _index, operation in _affirmed_operation_items(intent)
    )
    positive_match = any(
        any(_contains(text, term) for term in _operation_terms(
            operation.intent_type, operation.action_type
        ))
        for operation in affirmed
    )
    if positive_match:
        return False
    return any(
        any(
            _contains(text, term)
            for term in _operation_terms(
                operation.intent_type, operation.action_type
            )
        )
        for operation in negated
    )


def _affirmed_operation_items(intent: ParsedIntent) -> list[tuple[int, Any]]:
    """Project effective affirmed operations with their original index.

    A non-retracted denial wins only when it addresses the same operation
    target, or when either side omits a target and cannot be distinguished
    safely.  This keeps ``run unit tests; don't run integration tests`` while
    still failing closed for ``run pytest; don't execute it``.  The parser's
    original operations remain untouched for diagnostics and telemetry.
    """
    operations = tuple(getattr(intent, "operations", ()) or ())
    active_denials = tuple(
        operation
        for operation in operations
        if getattr(getattr(operation, "polarity", None), "value", None)
        == "negated"
        and not any(
            str(code).startswith("explicit:retracted-")
            for code in tuple(getattr(operation, "evidence_codes", ()) or ())
        )
    )
    items: list[tuple[int, Any]] = []
    for index, operation in enumerate(operations):
        polarity = getattr(operation, "polarity", None)
        pair = (
            getattr(operation, "intent_type", None),
            getattr(operation, "action_type", None),
        )
        if getattr(polarity, "value", polarity) != "affirmed":
            continue
        denied_same_target = any(
            pair
            == (
                getattr(denial, "intent_type", None),
                getattr(denial, "action_type", None),
            )
            and _operation_targets_overlap(operation, denial)
            for denial in active_denials
        )
        if not denied_same_target:
            items.append((index, operation))
    return items


_OPERATION_TARGET_STOPWORDS = frozenset(
    {
        "it",
        "them",
        "this",
        "that",
        "these",
        "those",
        "same",
        "again",
        "please",
        "now",
        "不要",
        "不用",
        "别",
        "它",
        "这个",
        "这些",
        "那些",
    }
)


def _operation_target_terms(operation: Any) -> tuple[str, ...]:
    """Extract conservative target evidence for same-pair conflict checks."""

    intent_type = getattr(operation, "intent_type", IntentType.UNKNOWN)
    action_type = getattr(operation, "action_type", ActionType.UNKNOWN)
    excluded = {
        _normalize_text(term).strip()
        for term in _operation_terms(intent_type, action_type)
    } | set(_GENERIC_ROUTING_KEYWORDS) | set(_OPERATION_TARGET_STOPWORDS)
    values = [
        *tuple(getattr(operation, "keywords", ()) or ()),
        *_entity_terms(getattr(operation, "entities", {}) or {}),
    ]
    targets: list[str] = []
    for raw_value in values:
        normalized = _normalize_text(str(raw_value).strip()).strip()
        if not normalized or normalized in excluded:
            continue
        if normalized.isascii():
            tokens = tuple(re.findall(r"[a-z0-9./:]+", normalized))
            retained = tuple(
                token
                for token in tokens
                if token not in excluded
                and token not in _OPERATION_TARGET_STOPWORDS
            )
            if not retained:
                continue
            normalized = " ".join(retained)
        if normalized not in targets:
            targets.append(normalized)
    return tuple(targets)


def _operation_targets_overlap(affirmed: Any, denied: Any) -> bool:
    affirmed_targets = _operation_target_terms(affirmed)
    denied_targets = _operation_target_terms(denied)
    if not affirmed_targets or not denied_targets:
        return True
    return any(
        left == right
        or _contains(left, right)
        or _contains(right, left)
        for left in affirmed_targets
        for right in denied_targets
    )


def _effective_routing_pair(
    intent: ParsedIntent,
) -> tuple[IntentType, ActionType]:
    """Return the first effective operation pair used by advisory ranking."""

    effective = _affirmed_operation_items(intent)
    if effective:
        operation = effective[0][1]
        return operation.intent_type, operation.action_type
    if not tuple(getattr(intent, "operations", ()) or ()):
        return intent.intent_type, intent.action_type
    return IntentType.UNKNOWN, ActionType.UNKNOWN


def _effective_routing_is_read_only(intent: ParsedIntent) -> bool:
    """Compute read-only status from the same operations Skills can see."""

    operations = tuple(getattr(intent, "operations", ()) or ())
    if not operations:
        return intent.is_read_only()
    effective = [operation for _index, operation in _affirmed_operation_items(intent)]
    return all(
        operation.action_type
        in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
        for operation in effective
    )


def _allows_inferred_skill_routing(intent: ParsedIntent) -> bool:
    """Return whether task interpretation may admit non-explicit Skills.

    Structured intents require at least one affirmed operation. A tuple with
    only negated operations therefore never creates candidates. The known
    scalar form is retained solely for pre-V2 callers that still construct a
    classified ``ParsedIntent`` without populating ``operations``.
    """
    operations = tuple(getattr(intent, "operations", ()) or ())
    if _affirmed_operation_items(intent):
        return True
    return not operations and intent.intent_type is not IntentType.UNKNOWN


def _operation_match_strength(
    text: str,
    operation: Any,
    *,
    alias: Any,
) -> tuple[float, float]:
    """Return query-specific and broad evidence for one operation."""
    specific = 0.0
    seen_specific: set[tuple[object, ...]] = set()
    for keyword in tuple(getattr(operation, "keywords", ()) or ()):
        normalized = str(keyword).strip()
        if (
            not normalized
            or _normalize_text(normalized) in _GENERIC_ROUTING_KEYWORDS
            or not _term_matches(text, normalized, alias)
        ):
            continue
        evidence_key = _semantic_evidence_key(normalized, alias)
        if evidence_key in seen_specific:
            continue
        seen_specific.add(evidence_key)
        specific += 1.0

    operation_entities = getattr(operation, "entities", {}) or {}
    for entity in _entity_terms(operation_entities):
        evidence_key = ("entity", _normalize_text(entity))
        if evidence_key in seen_specific or not _term_matches(text, entity, alias):
            continue
        seen_specific.add(evidence_key)
        specific += 1.5

    broad = 0.0
    confidence = getattr(operation, "confidence", 0.0)
    try:
        confident = float(confidence) >= _MIN_CONFIDENCE_INTENT_SIGNAL
    except (TypeError, ValueError):
        confident = False
    if confident:
        intent_type = getattr(operation, "intent_type", IntentType.UNKNOWN)
        action_type = getattr(operation, "action_type", ActionType.UNKNOWN)
        broad = float(
            sum(
                1
                for term in _operation_terms(intent_type, action_type)
                if _contains(text, term)
            )
        )
    return specific, broad


def _routing_context_topic_text(intent: ParsedIntent) -> str:
    semantic_query = str(getattr(intent, "semantic_query", "") or "")
    marker = "\nContext topics:"
    if marker not in semantic_query:
        return ""
    return semantic_query.split(marker, 1)[1].strip()


def _operation_neutral_routing_context(
    intent: ParsedIntent,
    current_operation: Any,
) -> str:
    """Return safe background plus other affirmed operation clauses.

    Quoted logs and source artifacts may be useful task context even when the
    parser correctly excludes them from the requested operation. Conversely,
    A prior affirmed clause may supply an omitted subject to the current one
    (``pytest failed; locate the cause and repair``).  A negated clause must
    never grant purpose authority.  The current clause is removed because the
    caller adds it back explicitly.
    """
    raw_input = str(getattr(intent, "raw_input", "") or "")
    operations = tuple(getattr(intent, "operations", ()) or ())
    if not operations:
        return raw_input
    effective_affirmed_ids = {
        id(operation) for _index, operation in _affirmed_operation_items(intent)
    }
    characters = list(raw_input)
    occupied: list[tuple[int, int]] = []
    for operation in operations:
        if (
            operation is not current_operation
            and id(operation) in effective_affirmed_ids
        ):
            continue
        span = getattr(operation, "span", None)
        start: int | None = None
        end: int | None = None
        if (
            isinstance(span, tuple)
            and len(span) == 2
            and all(isinstance(value, int) for value in span)
            and 0 <= span[0] <= span[1] <= len(raw_input)
        ):
            start, end = span
        else:
            clause = str(getattr(operation, "clause", "") or "")
            if clause:
                candidate = raw_input.find(clause)
                if candidate >= 0:
                    start, end = candidate, candidate + len(clause)
        if start is None or end is None:
            continue
        if any(start < prior_end and prior_start < end for prior_start, prior_end in occupied):
            continue
        occupied.append((start, end))
        characters[start:end] = " " * (end - start)
    return "".join(characters).strip()


def _allows_purpose_only_fallback(
    intent: ParsedIntent,
    operation: Any,
) -> bool:
    """Require engineering context before purpose prose can admit a Skill.

    A topic-compatible Skill is admitted before this fallback is considered.
    This gate therefore only prevents generic coding workflows from claiming
    non-engineering requests such as news search or creative writing.
    """
    clause = str(getattr(operation, "clause", "") or "")
    if _UNKNOWN_NON_TASK_CUES.search(clause):
        return False
    if (
        getattr(operation, "intent_type", None) is IntentType.CODE
        and getattr(operation, "action_type", None)
        not in {ActionType.MOVE, ActionType.RENAME}
    ):
        # A concrete move/rename has a narrow, recognizable workflow even
        # when its filename or symbol is absent from generic Skill prose.
        # Other code mutations still require real topic evidence.
        return False
    contextual_operation = bool(getattr(intent, "context_used", False)) and any(
        str(code).startswith("context:")
        for code in tuple(getattr(operation, "evidence_codes", ()) or ())
    )
    current_turn_authority = any(
        str(code).startswith(("rule:", "explicit:"))
        for code in tuple(getattr(operation, "evidence_codes", ()) or ())
    )
    if (
        contextual_operation
        and not current_turn_authority
        and not any((getattr(operation, "entities", {}) or {}).values())
        and not (
            getattr(operation, "intent_type", None) is IntentType.EXPLAIN
            and getattr(operation, "action_type", None) is ActionType.READ
        )
    ):
        # Context may safely recover a requested operation, but a generic
        # purpose-only Skill would turn a vague reference into new authority.
        # Contextual routes therefore still require a real topic match.
        return False
    # Only this affirmed clause and operation-neutral background may authorize
    # its purpose lane. A separate negated operation ("do not optimize the
    # database") must not create engineering authority for an unrelated
    # affirmative search.
    evidence = " ".join(
        [
            clause,
            _operation_neutral_routing_context(intent, operation),
            *_entity_terms(getattr(operation, "entities", {}) or {}),
            _routing_context_topic_text(intent),
        ]
    )
    if (
        _NON_ENGINEERING_PURPOSE_ONLY_CUES.search(evidence)
        and not _STRONG_ENGINEERING_PURPOSE_ONLY_CUES.search(evidence)
    ):
        # Polysemous words such as "database" do not turn a restaurant or
        # travel search into a codebase task. A concrete software cue (API,
        # source file, function, SQL, and so on) can disambiguate it.
        return False
    return bool(
        _ENGINEERING_PURPOSE_ONLY_CUES.search(evidence)
        or _CJK_ADJACENT_ENGINEERING_TOKEN_RE.search(evidence)
    )


def _skill_activation_satisfied(
    skill: dict,
    intent: ParsedIntent,
) -> bool:
    """Apply an optional catalog-declared one-of activation contract.

    Broad operation contracts describe what a Skill *can* do.  Some Skills
    still require a narrower user goal (for example teaching rather than any
    explanation).  ``activation_terms`` expresses that distinction without
    hard-coding a Skill name in the router.  Explicit invocation remains the
    user's authority to bypass inferred activation.
    """

    activation_terms = _list_field(skill, "activation_terms")
    if not activation_terms:
        return True
    affirmed_items = _affirmed_operation_items(intent)
    affirmed_keywords = [
        str(keyword)
        for _index, operation in affirmed_items
        for keyword in tuple(getattr(operation, "keywords", ()) or ())
        if str(keyword).strip()
    ]
    neutral_subjects = [
        _mask_quoted_authority_text(
            _operation_neutral_routing_context(intent, operation)
        )
        for _index, operation in affirmed_items
    ]
    query = _normalize_text(
        " ".join(
            [
                _mask_quoted_authority_text(_affirmed_routing_query(intent)),
                *affirmed_keywords,
                *neutral_subjects,
            ]
        )
    )
    # Activation is a catalog admission contract, not a ranking hint.  A
    # broad semantic group (for example ``regression`` sharing a performance
    # group with ``p95``) must not silently satisfy it. Catalog authors list
    # bilingual/synonymous forms explicitly when they are intended gates.
    return any(_contains(query, term) for term in activation_terms)


def _operation_topic_strength(
    text: str,
    operation: Any,
    *,
    alias: Any,
    purpose_terms: set[str],
) -> float:
    """Query-specific subject overlap without counting action aliases."""
    specific = 0.0
    seen: set[tuple[object, ...]] = set()
    normalized_purpose_terms = {
        _normalize_text(term).strip() for term in purpose_terms
    }
    non_topic_semantic_groups: set[int] = set()
    if alias is not None:
        query_groups = getattr(alias, "query_groups", None)
        if callable(query_groups):
            non_topic_semantic_groups = set(
                query_groups(
                    " ".join(
                        [
                            *sorted(purpose_terms),
                            "code file module project skill workflow",
                        ]
                    )
                )
            )
    for keyword in tuple(getattr(operation, "keywords", ()) or ()):
        normalized = str(keyword).strip()
        normalized_key = _normalize_text(normalized).strip()
        if (
            not normalized
            or normalized_key in _GENERIC_ROUTING_KEYWORDS
            or normalized_key in normalized_purpose_terms
        ):
            continue
        match_weight = _topic_term_match_weight(text, normalized, alias)
        if (
            match_weight <= 0.0
            and alias is not None
            and not normalized.isascii()
        ):
            matched_group_ids = getattr(alias, "matched_group_ids", None)
            if callable(matched_group_ids):
                semantic_topic_groups = set(
                    matched_group_ids(normalized, text)
                ) - non_topic_semantic_groups
                match_weight = _ALIAS_ONLY_TOPIC_TERM_WEIGHT * min(
                    len(semantic_topic_groups),
                    _MAX_SEMANTIC_TOPIC_GROUPS,
                )
        if match_weight <= 0.0:
            continue
        evidence_key = _semantic_evidence_key(normalized, alias)
        if evidence_key in seen:
            continue
        seen.add(evidence_key)
        specific += match_weight

    operation_entities = getattr(operation, "entities", {}) or {}
    for entity_kind, values in operation_entities.items():
        weight = 0.5 if str(entity_kind) == "languages" else 1.5
        for entity in _entity_terms({str(entity_kind): list(values)}):
            evidence_key = _semantic_evidence_key(entity, alias)
            if evidence_key in seen or not _term_matches(text, entity, alias):
                continue
            seen.add(evidence_key)
            specific += weight
    return specific


def _operation_topic_query(
    operation: Any,
    *,
    purpose_terms: set[str],
    alias: Any,
) -> str:
    """Project subject terms without the operation's literal action cues."""
    topic_bearing_purpose_terms = {
        "failure",
        "performance",
        "regression",
        "slow",
    }
    action_terms = purpose_terms - topic_bearing_purpose_terms
    excluded = {
        _normalize_text(term).strip()
        for term in action_terms
    } | set(_GENERIC_ROUTING_KEYWORDS)
    values: list[str] = []
    seen: set[str] = set()
    raw_values = [
        *tuple(getattr(operation, "keywords", ()) or ()),
        *_entity_terms(getattr(operation, "entities", {}) or {}),
    ]
    for raw in raw_values:
        value = str(raw).strip()
        normalized = _normalize_text(value).strip()
        if not value or normalized in excluded or normalized in seen:
            continue
        clause = str(getattr(operation, "clause", "") or "")
        if re.search(
            rf"\$\s*`?{re.escape(value)}`?(?![a-z0-9._/:-])",
            clause,
            re.IGNORECASE,
        ):
            continue
        if alias is not None:
            related_terms = getattr(alias, "related_terms", None)
            if (
                not normalized.isascii()
                and callable(related_terms)
                and not related_terms(value)
            ):
                # Raw CJK tokenization intentionally keeps whole clauses.
                # Only exact curated concepts belong in semantic topic text;
                # otherwise a substring such as 回滚 inside 可回滚记录 can
                # activate the wrong domain despite a more specific alias.
                continue
            query_groups = getattr(alias, "query_groups", None)
            if callable(query_groups):
                value_groups = set(query_groups(value))
                purpose_groups = set(
                    query_groups(" ".join(sorted(action_terms)))
                )
                if value_groups and value_groups.issubset(purpose_groups):
                    continue
        seen.add(normalized)
        values.append(value)
    return " ".join(values)


def _operation_skill_matches(
    skill: dict,
    intent: ParsedIntent,
    *,
    alias: Any,
) -> tuple[_OperationSkillMatch, ...]:
    """Score each affirmed operation against clean topic and purpose views."""
    topic_text = _skill_topic_text(skill)
    identity_topic_text = _skill_identity_topic_text(skill)
    purpose_text = _skill_purpose_text(skill)
    declared_operations = _declared_supported_operations(skill)
    context_topics = _routing_context_topic_text(intent)
    matches: list[_OperationSkillMatch] = []
    prior_affirmed_topic_query = ""
    prior_affirmed_topic_score = 0.0
    for operation_index, operation in _affirmed_operation_items(intent):
        purpose_terms = _operation_purpose_terms(
            operation.intent_type,
            operation.action_type,
        )
        current_topic_query = _operation_topic_query(
            operation,
            purpose_terms=purpose_terms,
            alias=alias,
        )
        topic_score = _operation_topic_strength(
            topic_text,
            operation,
            alias=alias,
            purpose_terms=purpose_terms,
        )
        topic_score += 0.5 * _operation_topic_strength(
            identity_topic_text,
            operation,
            alias=None,
            purpose_terms=purpose_terms,
        )
        if not current_topic_query and prior_affirmed_topic_query:
            # The nearest earlier affirmative operation may supply an omitted
            # subject.  Preserve each Skill's own prior topic strength so a
            # generic "test" workflow cannot tie a pytest-specific workflow.
            topic_score += prior_affirmed_topic_score
        semantic_group_count = 0
        if alias is not None:
            clause_query = current_topic_query or prior_affirmed_topic_query
            if context_topics:
                clause_query = f"{clause_query} {context_topics}".strip()
            matched_groups = set(
                alias.matched_group_ids(clause_query, topic_text)
            )
            generic_groups = set(
                alias.query_groups("code file project skill workflow")
            )
            semantic_group_count = min(
                _MAX_SEMANTIC_TOPIC_GROUPS,
                len(matched_groups - generic_groups),
            )
            if topic_score <= 0.0:
                topic_score += (
                    semantic_group_count * _SEMANTIC_TOPIC_GROUP_WEIGHT
                )
            else:
                # Lexical/curated-alias evidence already represents this
                # concept. Do not count the same alias group a second time;
                # exact ``pytest`` should outrank a generic ``test`` match.
                semantic_group_count = 0
        if current_topic_query:
            prior_affirmed_topic_query = current_topic_query
            prior_affirmed_topic_score = topic_score
        purpose_compatible = (
            (operation.intent_type, operation.action_type)
            in declared_operations
            if declared_operations is not None
            else any(
                _contains(purpose_text, term)
                for term in purpose_terms
            )
        )
        # Domain gating is local to this operation. Other affirmed lanes may
        # provide a missing subject for ranking, but must not turn an
        # independent lifestyle task into engineering work (for example,
        # ``explain API; write a travel guide``).
        operation_domain_evidence = " ".join(
            [
                str(getattr(operation, "clause", "") or ""),
                *(
                    str(keyword)
                    for keyword in tuple(
                        getattr(operation, "keywords", ()) or ()
                    )
                ),
                *_entity_terms(getattr(operation, "entities", {}) or {}),
            ]
        )
        if (
            _NON_ENGINEERING_PURPOSE_ONLY_CUES.search(
                operation_domain_evidence
            )
            and not _STRONG_ENGINEERING_PURPOSE_ONLY_CUES.search(
                operation_domain_evidence
            )
            and _STRONG_ENGINEERING_PURPOSE_ONLY_CUES.search(topic_text)
        ):
            # Operation labels such as document.update and review.compare
            # are domain-neutral.  A picnic checklist or travel comparison
            # must not select an engineering workflow merely because it
            # shares those verbs; a domain-matched non-engineering Skill can
            # still participate because its catalog text lacks code cues.
            topic_score = 0.0
            semantic_group_count = 0
            purpose_compatible = False
        matches.append(
            _OperationSkillMatch(
                operation_index=operation_index,
                topic_score=topic_score,
                purpose_compatible=purpose_compatible,
                semantic_topic_groups=semantic_group_count,
            )
        )
    return tuple(matches)


def _apply_operation_topic_purpose_scores(
    scored: list[_ScoredSkill],
    skills: list[dict],
    intent: ParsedIntent,
    *,
    alias: Any,
    eligible_catalog_indexes: set[int],
) -> list[_ScoredSkill]:
    """Make operation × topic causal for admission, ranking, and lanes.

    A legacy Skill without an operation contract may remain a topic-only
    compatibility fallback when the catalog has no purpose-compatible option.
    Declared contracts are authoritative, including malformed declarations,
    and never borrow compatibility from another operation. QUESTION, CHAT,
    and UNKNOWN operations never activate the fallback lane.
    """
    operation_items = _affirmed_operation_items(intent)
    if not operation_items:
        return scored

    matches_by_catalog_index = {
        item[4]: _operation_skill_matches(
            skills[item[4]],
            intent,
            alias=alias,
        )
        for item in scored
    }
    contracted_catalog_indexes = {
        item[4]
        for item in scored
        if _declared_supported_operations(skills[item[4]]) is not None
    }
    compatible_operation_indexes = {
        match.operation_index
        for matches in matches_by_catalog_index.values()
        for match in matches
        if match.topic_score > 0.0 and match.purpose_compatible
    }
    eligible_compatible_operation_indexes = {
        match.operation_index
        for catalog_index, matches in matches_by_catalog_index.items()
        if catalog_index in eligible_catalog_indexes
        for match in matches
        if match.topic_score > 0.0 and match.purpose_compatible
    }
    strict_purpose_operation_indexes = {
        operation_index
        for operation_index, operation in operation_items
        if not _allows_purpose_only_fallback(intent, operation)
    }
    operation_types = {
        index: operation.intent_type for index, operation in operation_items
    }
    purpose_only_winners: dict[int, set[int]] = {}
    for operation_index, operation in operation_items:
        if (
            operation_index in eligible_compatible_operation_indexes
            or operation.intent_type not in _PURPOSE_ONLY_FALLBACK_INTENTS
            or not _allows_purpose_only_fallback(intent, operation)
        ):
            continue
        choices = [
            item
            for item in scored
            if item[4] in eligible_catalog_indexes
            and any(
                match.operation_index == operation_index
                and match.topic_score <= 0.0
                and match.purpose_compatible
                for match in matches_by_catalog_index[item[4]]
            )
        ]
        if choices and operation.intent_type is IntentType.REVIEW:
            general_review_choices = [
                item
                for item in choices
                if _declares_general_review_purpose(skills[item[4]])
            ]
            if general_review_choices:
                # Reaching this lane means no compatible Skill matched the
                # requested subject.  Prefer an explicitly general code/design
                # review contract over a specialized audit whose larger tool
                # list happens to earn more capability-affinity points.
                choices = general_review_choices
            elif any(
                match.operation_index == operation_index
                and match.topic_score > 0.0
                for matches in matches_by_catalog_index.values()
                for match in matches
            ):
                # A compound request may name one subject for several
                # operations while only one operation has a matching Skill
                # contract.  Leave the unsupported review lane empty instead
                # of assigning it either to that Skill or to an unrelated,
                # specialized audit purely because its prose says "review".
                choices = []
        elif operation_index in compatible_operation_indexes:
            # The catalog recognizes this subject, but its matching workflow
            # is unavailable. A broad operation contract cannot authorize an
            # unrelated specialty to take over. The explicitly general review
            # purpose above is a declared fallback, so it remains eligible.
            choices = []
        if (
            len(choices) > 1
            and operation.intent_type is IntentType.TEST
            and operation.action_type is ActionType.EXECUTE
        ):
            # With no topic evidence, operation compatibility alone cannot
            # distinguish several workflows.  Tool count and capability
            # breadth are implementation details, not user intent.  Leave the
            # lane empty unless an earlier specialization rule reduced it to
            # one unambiguous catalog choice.
            choices = []
        if choices:
            strongest_specific = max(item[2] for item in choices)
            winners = {
                item[4]
                for item in choices
                if item[2]
                >= max(
                    _MIN_SPECIFIC_SIGNAL,
                    strongest_specific - _SPECIFIC_SIGNAL_MARGIN,
                )
            }
            if not winners:
                winners = {
                    max(
                        choices,
                        key=lambda item: (
                            item[2],
                            item[0].score,
                            _SOURCE_PRIORITY.get(item[0].source, -1),
                            -item[4],
                        ),
                    )[4]
                }
            purpose_only_winners[operation_index] = winners
            compatible_operation_indexes.add(operation_index)

    adjusted: list[_ScoredSkill] = []
    for routed, signal_score, old_specific, affinity_score, catalog_index in scored:
        matches = matches_by_catalog_index[catalog_index]
        # A whole-request embedding score has no clause identity. It can be
        # used as topic evidence for a single operation, but broadcasting it
        # across a compound request would falsely attach every semantic hit
        # to every operation lane.
        embedding_topic_score = (
            1.5
            if len(operation_items) == 1 and any(
                reason.startswith("semantic:embedding(")
                for reason in routed.reasons
            )
            else 0.0
        )
        effective: list[
            tuple[int, float, _OperationSkillMatch, float, bool]
        ] = []
        for match in matches:
            topic_score = match.topic_score + embedding_topic_score
            if (
                topic_score <= 0.0
                and match.purpose_compatible
                and catalog_index
                in purpose_only_winners.get(match.operation_index, set())
            ):
                topic_score = _PURPOSE_ONLY_TOPIC_FLOOR
            if topic_score <= 0.0:
                continue
            requires_compatible = (
                catalog_index in contracted_catalog_indexes
                or match.operation_index in compatible_operation_indexes
                or match.operation_index in strict_purpose_operation_indexes
                or operation_types.get(match.operation_index)
                in {IntentType.UNKNOWN, IntentType.QUESTION, IntentType.CHAT}
            )
            purpose_compatible = match.purpose_compatible
            if requires_compatible and not purpose_compatible:
                continue
            joint_score = topic_score
            if purpose_compatible:
                joint_score += _OPERATION_PURPOSE_BONUS
            effective.append(
                (
                    match.operation_index,
                    joint_score,
                    match,
                    topic_score,
                    purpose_compatible,
                )
            )

        routed.operation_indexes = tuple(item[0] for item in effective)
        routed.operation_topic_scores = tuple(
            (item[0], item[3]) for item in effective
        )
        routed.topic_specific_score = max(
            (item[3] for item in effective),
            default=0.0,
        )
        if routed.explicitly_requested:
            adjusted.append(
                (routed, signal_score, old_specific, affinity_score, catalog_index)
            )
            continue

        new_specific = max((item[1] for item in effective), default=0.0)
        routed.score = round(routed.score + new_specific - old_specific, 3)
        if effective:
            if any(item[4] for item in effective):
                routed.reasons.append("operation-purpose-compatible")
            if any(item[2].semantic_topic_groups for item in effective):
                routed.reasons.append("semantic:alias:operation-topic")
        elif old_specific > 0.0:
            routed.reasons.append("operation-purpose-suppressed")
        adjusted.append(
            (routed, signal_score, new_specific, affinity_score, catalog_index)
        )
    return adjusted


def _candidate_operation_indexes(
    text: str,
    intent: ParsedIntent,
    *,
    alias: Any,
    supported_operations: frozenset[
        tuple[IntentType, ActionType]
    ] | None = None,
) -> tuple[int, ...]:
    """Associate a candidate only with affirmed request operations.

    Query-specific keyword/entity evidence takes precedence. Broad
    intent/action terms are a conservative fallback for terse operations.
    """
    strengths = [
        (
            index,
            *_operation_match_strength(text, operation, alias=alias),
        )
        for index, operation in _affirmed_operation_items(intent)
        if (
            supported_operations is None
            or (operation.intent_type, operation.action_type)
            in supported_operations
        )
    ]
    specific_indexes = tuple(
        index for index, specific, _broad in strengths if specific > 0.0
    )
    if specific_indexes:
        return specific_indexes
    return tuple(index for index, _specific, broad in strengths if broad > 0.0)


def _round_robin_operation_candidates(
    ranked: list[_ScoredSkill],
    skills: list[dict],
    intent: ParsedIntent,
    *,
    alias: Any,
) -> list[_ScoredSkill]:
    """Merge per-operation lanes fairly while retaining deterministic ties."""
    operation_items = _affirmed_operation_items(intent)
    operation_indexes = [index for index, _operation in operation_items]
    for item in ranked:
        if not item[0].operation_indexes:
            item[0].operation_indexes = _candidate_operation_indexes(
                _skill_topic_text(skills[item[4]]),
                intent,
                alias=alias,
                supported_operations=_declared_supported_operations(
                    skills[item[4]]
                ),
            )
    if len(operation_indexes) <= 1:
        return ranked

    buckets = {
        operation_index: [
            item
            for item in ranked
            if operation_index in item[0].operation_indexes
        ]
        for operation_index in operation_indexes
    }
    cursors = {operation_index: 0 for operation_index in operation_indexes}
    merged: list[_ScoredSkill] = []
    seen_catalog_indexes: set[int] = set()

    while True:
        made_progress = False
        for operation_index in operation_indexes:
            bucket = buckets[operation_index]
            cursor = cursors[operation_index]
            while cursor < len(bucket):
                item = bucket[cursor]
                cursor += 1
                if item[4] in seen_catalog_indexes:
                    continue
                seen_catalog_indexes.add(item[4])
                merged.append(item)
                made_progress = True
                break
            cursors[operation_index] = cursor
        if not made_progress:
            break

    # Retain admitted candidates that could not be tied to a single clause;
    # they remain after the fair per-operation lanes in legacy ranking order.
    for item in ranked:
        if item[4] not in seen_catalog_indexes:
            seen_catalog_indexes.add(item[4])
            merged.append(item)
    return merged


def _prune_deterministic_operation_selection(
    ranked: list[_ScoredSkill],
    *,
    explicit: list[_ScoredSkill],
    intent: ParsedIntent,
    skills: list[dict],
) -> list[_ScoredSkill]:
    """Keep only competitive candidates and their competitive lane bindings.

    The operation round-robin still preserves compound-task coverage, while
    weak cross-topic aliases are removed from both prompt selection and the
    candidate's operation metadata. Equal-strength alternatives remain, so
    genuinely indistinguishable catalog entries keep deterministic top-k
    behavior.
    """
    operation_indexes = [
        index for index, _operation in _affirmed_operation_items(intent)
    ]
    if not ranked or not operation_indexes:
        return ranked

    def operation_topic_score(
        routed: RoutedSkill,
        operation_index: int,
    ) -> float:
        by_operation = dict(routed.operation_topic_scores)
        if operation_index in by_operation:
            return max(0.0, by_operation[operation_index])
        return max(0.0, routed.topic_specific_score)

    strongest_by_operation: dict[int, float] = {}
    for item in [*explicit, *ranked]:
        routed = item[0]
        for operation_index in routed.operation_indexes:
            topic_score = operation_topic_score(routed, operation_index)
            if topic_score <= 0.0:
                continue
            strongest_by_operation[operation_index] = max(
                strongest_by_operation.get(operation_index, 0.0),
                topic_score,
            )
    if not strongest_by_operation:
        return ranked

    selected: list[_ScoredSkill] = []
    for item in ranked:
        routed = item[0]
        if not routed.operation_indexes:
            selected.append(item)
            continue
        competitive_indexes = tuple(
            operation_index
            for operation_index in routed.operation_indexes
            if (
                operation_topic_score(routed, operation_index)
                >= strongest_by_operation.get(
                    operation_index,
                    operation_topic_score(routed, operation_index),
                )
                - _SPECIFIC_SIGNAL_MARGIN
            )
        )
        if competitive_indexes:
            competitive_set = set(competitive_indexes)
            routed.operation_indexes = competitive_indexes
            routed.operation_topic_scores = tuple(
                (operation_index, score)
                for operation_index, score in routed.operation_topic_scores
                if operation_index in competitive_set
            )
            routed.topic_specific_score = max(
                (score for _operation_index, score in routed.operation_topic_scores),
                default=routed.topic_specific_score,
            )
            selected.append(item)
    # Prefer one high-confidence Skill that covers a strict superset of the
    # same operation lanes over a redundant subset Skill.  This applies only
    # when the superset is at least as topic-specific on every shared lane;
    # weak purpose-only fallbacks and explicit requests are intentionally
    # outside this inferred-candidate pruning path.
    dominated_catalog_indexes: set[int] = set()
    for candidate in selected:
        candidate_indexes = set(candidate[0].operation_indexes)
        if (
            not candidate_indexes
            or _list_field(skills[candidate[4]], "activation_terms")
        ):
            continue
        candidate_scores = dict(candidate[0].operation_topic_scores)
        for alternative in selected:
            if alternative is candidate:
                continue
            alternative_indexes = set(alternative[0].operation_indexes)
            activation_terms = _list_field(
                skills[alternative[4]],
                "activation_terms",
            )
            if (
                alternative_indexes != candidate_indexes
                or not activation_terms
                or not _skill_activation_satisfied(
                    skills[alternative[4]],
                    intent,
                )
            ):
                continue
            alternative_scores = dict(
                alternative[0].operation_topic_scores
            )
            if all(
                alternative_scores.get(index, 0.0)
                >= candidate_scores.get(index, 0.0)
                for index in candidate_indexes
            ):
                # A matched activation contract is explicit catalog evidence
                # that this workflow is the specialized form of an otherwise
                # equal generic candidate on the same operation lanes.
                dominated_catalog_indexes.add(candidate[4])
                break
    for candidate in selected:
        candidate_indexes = set(candidate[0].operation_indexes)
        if not candidate_indexes:
            continue
        candidate_scores = dict(candidate[0].operation_topic_scores)
        if any(
            score < _WEAK_SPECIFIC_SIGNAL_THRESHOLD
            for score in candidate_scores.values()
        ):
            continue
        for alternative in selected:
            if alternative is candidate:
                continue
            alternative_indexes = set(alternative[0].operation_indexes)
            alternative_scores = dict(alternative[0].operation_topic_scores)
            if not candidate_indexes < alternative_indexes:
                continue
            if any(
                alternative_scores.get(index, 0.0)
                < candidate_scores.get(index, 0.0)
                for index in candidate_indexes
            ):
                continue
            dominated_catalog_indexes.add(candidate[4])
            break
    return [
        item for item in selected if item[4] not in dominated_catalog_indexes
    ]


def _opaque_candidate_id(skill: RoutedSkill, catalog_index: int) -> str:
    identity = "\0".join(
        (
            str(catalog_index),
            skill.source,
            skill.qualified_name or skill.name,
            skill.path,
            skill.routing_identity_digest or skill.content_digest,
        )
    )
    return f"skill_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _rerank_candidate_description(skill: dict) -> str:
    """Build a bounded semantic view; the remote adapter treats it as data."""
    fields = [
        str(skill.get("name", "")),
        str(skill.get("description", "")),
        str(skill.get("directory", "")),
        " ".join(_list_field(skill, "keywords")),
        " ".join(_list_field(skill, "examples")),
        " ".join(_list_field(skill, "domains")),
        " ".join(_list_field(skill, "scopes")),
    ]
    return " ".join(" ".join(fields).split())[:2_000]


def _reranker_decision_reason(decision: SkillRerankDecision) -> str:
    reason_codes = tuple(decision.reason_codes)[:8]
    if not reason_codes:
        return decision.status
    return f"{decision.status}:{','.join(reason_codes)}"


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

    operations = tuple(getattr(intent, "operations", ()) or ())
    affirmed_operations = tuple(
        operation for _index, operation in _affirmed_operation_items(intent)
    )
    term_confidences: dict[str, float] = {}
    if affirmed_operations:
        for operation in affirmed_operations:
            term_confidences[operation.intent_type.value] = max(
                term_confidences.get(operation.intent_type.value, 0.0),
                operation.confidence,
            )
            term_confidences[operation.action_type.value] = max(
                term_confidences.get(operation.action_type.value, 0.0),
                operation.confidence,
            )
    elif not operations:
        term_confidences = {
            intent.intent_type.value: intent.confidence,
            intent.action_type.value: intent.confidence,
        }
    for term, term_confidence in term_confidences.items():
        if (
            term != "unknown"
            and term_confidence >= _MIN_CONFIDENCE_INTENT_SIGNAL
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
    only_negated_operations = bool(getattr(intent, "operations", ())) and not (
        affirmed_operations
    )
    effective_intent_type, _effective_action_type = _effective_routing_pair(intent)
    if effective_intent_type != IntentType.UNKNOWN:
        routing_query = _affirmed_routing_query(intent)
        seen_keyword_signals: set[tuple[object, ...]] = set()
        for keyword in _affirmed_routing_keywords(intent):
            if _normalize_text(keyword) in _GENERIC_ROUTING_KEYWORDS:
                continue
            if _term_matches(text, keyword, alias):
                evidence_key = _semantic_evidence_key(keyword, alias)
                if evidence_key in seen_keyword_signals:
                    continue
                seen_keyword_signals.add(evidence_key)
                score += 1.0
                signal_score += 1.0
                specific_score += 1.0
                reasons.append(f"keyword:{keyword}")
                matched_group_ids = getattr(alias, "matched_group_ids", None)
                if (
                    callable(matched_group_ids)
                    and not _contains(routing_query, keyword)
                    and not any(
                        reason.startswith("semantic:alias:")
                        for reason in reasons
                    )
                ):
                    try:
                        alias_groups = matched_group_ids(routing_query, keyword)
                    except Exception:  # noqa: BLE001 - optional rank helper
                        alias_groups = ()
                    if alias_groups:
                        reasons.append(
                            "semantic:alias:keyword-provenance"
                        )

        for entity in _entity_terms(_affirmed_routing_entities(intent)):
            if _term_matches(text, entity, alias):
                score += 1.5
                signal_score += 1.5
                specific_score += 1.5
                reasons.append(f"entity:{entity}")
    elif alias is not None and not only_negated_operations:
        query_text = _mask_quoted_authority_text(
            str(getattr(intent, "raw_input", "") or "")
        )
        if (
            not is_clause_negated(query_text)
            and not _UNKNOWN_NON_TASK_CUES.search(query_text.lower())
            and _UNKNOWN_TASK_CUES.search(query_text)
        ):
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


def _topic_term_match_weight(text: str, term: str, alias: Any) -> float:
    """Prefer exact topic evidence while retaining bilingual recall."""
    if _contains(text, term):
        return 1.0
    if alias is None:
        return 0.0
    for related in alias.related_terms(term):
        if _contains(text, related):
            return _ALIAS_ONLY_TOPIC_TERM_WEIGHT
    return 0.0


def _term_matches(text: str, term: str, alias: Any) -> bool:
    """Literal containment, or a cross-language concept alias hit."""
    return _topic_term_match_weight(text, term, alias) > 0.0


def _semantic_evidence_key(term: str, alias: Any) -> tuple[object, ...]:
    """Collapse synonymous keyword spellings into one evidence unit."""
    query_groups = getattr(alias, "query_groups", None)
    if callable(query_groups):
        try:
            groups = tuple(sorted(query_groups(term)))
        except Exception:  # noqa: BLE001 - aliases are optional rank helpers
            groups = ()
        if groups:
            return ("concept", *groups)
    return ("term", _normalize_text(term).strip())


def _is_explicit_skill_reference(
    raw_input: str,
    name: str,
    qualified_name: str,
    *,
    allow_bare_reference: bool = True,
) -> bool:
    """Return whether the user deliberately invoked this Skill.

    A bare word occurrence is only relevance evidence.  Treating it as an
    explicit invocation let a project Skill named ``test`` or ``review``
    acquire load-before-final authority from ordinary prose.  Explicit
    authority therefore requires an invocation with an explicit Skill label,
    a ``$`` sigil, or an input consisting solely of the identifier.
    """
    return (
        _explicit_skill_directive(
            raw_input,
            name,
            qualified_name,
            allow_bare_reference=allow_bare_reference,
        )
        == "requested"
    )


_SOURCE_ARTIFACT_IDENTIFIER_RE = re.compile(
    r"\.(?:c|cc|cfg|cpp|cs|css|go|h|hpp|html|ini|java|js|jsx|json|kt|"
    r"kts|lock|md|php|ps1|py|pyi|rb|rs|rst|scala|scss|sh|sql|svelte|"
    r"swift|toml|ts|tsx|txt|vue|xml|ya?ml|zsh)(?::\d+(?::\d+)?)?$",
    re.IGNORECASE,
)


def _looks_like_unlabelled_skill_identifier(identifier: str) -> bool:
    """Distinguish qualified Skill IDs from ordinary source artifacts.

    ``Use auth.py to ...`` describes which file to use, not a Skill named
    ``auth.py``.  A sigil or explicit Skill/技能 label can still deliberately
    request such an unusually named Skill.
    """
    normalized = str(identifier).strip("` ")
    return bool(
        re.search(r"[-_/:.]", normalized)
        and not _SOURCE_ARTIFACT_IDENTIFIER_RE.search(normalized)
    )


def _explicit_skill_directive(
    raw_input: str,
    name: str,
    qualified_name: str,
    *,
    allow_bare_reference: bool = True,
) -> str:
    """Return the final current-turn directive for one Skill candidate.

    Positive ambiguous bare names remain non-authoritative. A negative bare
    directive is fail-closed for every owner, while a later explicit positive
    directive can intentionally restore the same Skill.
    """
    if not raw_input.strip():
        return "none"
    identifiers: dict[str, bool] = {}
    normalized_name = _normalize_authority_text(name).strip()
    normalized_qualified_name = _normalize_authority_text(qualified_name).strip()
    if qualified_name and qualified_name.strip():
        identifiers[qualified_name.strip()] = (
            allow_bare_reference
            or normalized_qualified_name != normalized_name
        )
    if name and name.strip():
        identifier = name.strip()
        identifiers[identifier] = (
            identifiers.get(identifier, False) or allow_bare_reference
        )
    normalized_input = _mask_quoted_authority_text(
        _normalize_authority_text(raw_input)
    )
    directives: list[tuple[int, int, str]] = []
    for identifier, positive_allowed in identifiers.items():
        normalized_identifier = _normalize_authority_text(identifier).strip()
        if not normalized_identifier:
            continue
        identifier_pattern = (
            rf"(?<![a-z0-9._/:-]){re.escape(normalized_identifier)}"
            rf"(?![a-z0-9._/:-])"
        )
        quoted_identifier = rf"`?\s*{identifier_pattern}\s*`?"
        structured_identifier = _looks_like_unlabelled_skill_identifier(
            normalized_identifier
        )
        if positive_allowed and re.fullmatch(
            rf"\s*{identifier_pattern}\s*[.!?。！？]?\s*",
            normalized_input,
        ):
            directives.append((0, len(normalized_input), "requested"))
        explicit_patterns = (
            # Codex/Claude-style sigil. Backticks alone are intentionally not
            # authority: users routinely quote commands and symbols in prose.
            rf"\$\s*{identifier_pattern}",
            # English invocation grammar, including "Use the X Skill".
            rf"\b(?:use|using|load|loading|invoke|invoking|apply|applying|"
            rf"follow|following|select|selecting|choose|choosing|include|"
            rf"including|switch(?:ing)?\s+to)\s+"
            rf"(?:the\s+)?(?:"
            rf"skill\s+(?:named\s+|called\s+)?{quoted_identifier}"
            rf"|{quoted_identifier}\s+skill)\b",
            rf"\bskill\s+(?:named\s+|called\s+){quoted_identifier}",
            # Chinese invocation grammar.  Merely discussing “技能路由” is
            # not enough; both an action verb and the 技能 label are required.
            rf"(?:正在)?(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)\s*(?:"
            rf"技能\s*(?:名为\s*)?{quoted_identifier}"
            rf"|{quoted_identifier}\s*技能)",
            # Cross-language UI copy is common in Chinese prompts.
            rf"(?:请\s*)?(?:正在)?(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)\s*"
            rf"{quoted_identifier}\s*(?:skill|技能)",
            # UI-style reverse wording: ``owner/name 用这个 Skill``.
            rf"{quoted_identifier}\s*(?:就)?(?:用|使用)\s*(?:这个|该)\s*"
            rf"(?:skill|技能)",
        )
        if positive_allowed and structured_identifier:
            # Exact unique catalog IDs are unambiguous authority even when a
            # user omits the redundant Skill/技能 label. Simple words such as
            # "test" remain label/sigil-only to avoid promoting ordinary prose.
            explicit_patterns += (
                rf"\b(?:use|using|load|loading|invoke|invoking|apply|applying|"
                rf"follow|following|select|selecting|choose|choosing|include|"
                rf"including|switch(?:ing)?\s+to)\s+(?:the\s+)?"
                rf"{quoted_identifier}",
                rf"(?:请\s*)?(?:正在)?(?:使用|用|调用|加载|应用|按照|选择|选用|"
                rf"改用|改为)\s*{quoted_identifier}",
            )
        for pattern in explicit_patterns:
            for match in re.finditer(pattern, normalized_input):
                if _explicit_match_is_example_mention(
                    normalized_input,
                    match.start(),
                    match.end(),
                ):
                    if _REPORTED_AUTHORITY_DENIAL_SUFFIX_RE.search(
                        normalized_input[
                            match.end() : min(
                                len(normalized_input), match.end() + 160
                            )
                        ]
                    ):
                        directives.append(
                            (match.start(), match.end(), "denied")
                        )
                    continue
                if _explicit_match_is_negated(
                    normalized_input, match.start(), match.end()
                ):
                    directives.append((match.start(), match.end(), "denied"))
                    continue
                if positive_allowed:
                    directives.append(
                        (match.start(), match.end(), "requested")
                    )
        directives.extend(
            _partial_skill_replacement_directives(
                normalized_input,
                identifier_pattern,
                positive_allowed=positive_allowed,
            )
        )
        if positive_allowed:
            directives.extend(
                _positive_skill_list_directives(
                    normalized_input,
                    identifier_pattern,
                )
            )
            directives.extend(
                _positive_skill_correction_directives(
                    normalized_input,
                    identifier_pattern,
                )
            )
        has_current_positive = any(
            directive == "requested"
            for _, _, directive in directives
        )
        directives.extend(
            _negative_skill_directives(
                normalized_input,
                identifier_pattern,
                allow_unlabelled=(
                    structured_identifier
                    or normalized_identifier != normalized_name
                    or has_current_positive
                ),
            )
        )
    if not directives:
        return "none"
    return max(
        enumerate(directives),
        key=lambda item: (item[1][0], item[1][1], item[0]),
    )[1][2]


_EXPLICIT_CLAUSE_BOUNDARY = re.compile(r"[.!?;\n。！？；—…]")
_EXPLICIT_POSITIVE_RESET = re.compile(
    r"\b(?:but|instead|however|then|rather|yet|while)\b|"
    r"(?:但是|但|而是|然后|随后|同时)"
)
_EXPLICIT_ACTION = re.compile(
    r"\b(?:use|using|load|loading|invoke|invoking|apply|applying|"
    r"follow|following|select|selecting|choose|choosing|include|including|"
    r"switch\s+to)\b|"
    r"(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)"
)
_EXPLICIT_CONTROL_ACTION = re.compile(
    r"\b(?:use|using|used|load|loading|loaded|invoke|invoking|invoked|"
    r"apply|applying|applied|follow|following|followed|select|selecting|"
    r"selected|choose|choosing|chosen|include|including|included|"
    r"switch\s+to|avoid|avoiding|avoided|skip|skipping|skipped|exclude|"
    r"excluding|excluded|refrain|forbid|forbids|forbidden|prohibit|"
    r"prohibits|prohibited|disallow|disallows|disallowed|ban|bans|banned|"
    r"cease|ceases|ceased|ceasing|discontinue|discontinues|discontinued|"
    r"discontinuing)\b|"
    r"(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为|避免|跳过|排除)"
)
_EXPLICIT_SKILL_DENIAL_PREFIX_RE = re.compile(
    r"\b(?:disable|block|ban|forbid)\s+(?:the\s+)?"
    r"(?:skill\s+(?:named\s+|called\s+)?)?$|"
    r"(?:禁用|停用|屏蔽|禁止(?:使用|加载|调用))\s*"
    r"(?:技能\s*(?:名为\s*)?)?$",
    re.IGNORECASE,
)
_EXPLICIT_SCOPE_ACTION = re.compile(
    rf"(?:{ACTION_MARKER_RE.pattern})|"
    r"\b(?:avoid|avoiding|skip|skipping|exclude|excluding|refrain|"
    r"cease|ceasing|discontinue|discontinuing)\b|"
    r"(?:避免|跳过|排除)",
    re.IGNORECASE,
)
_EXPLICIT_COORDINATE_RESET = re.compile(
    r"\s*(?:,|，|、|\band\b|\bor\b|并且|或者|并|且|和|或)\s*",
    re.IGNORECASE,
)
_EXPLICIT_EXAMPLE_PREFIX_RE = re.compile(
    r"(?:例如|比如|举例|示例\s*[:：]?|"
    r"(?:例子|案例|文档|说明|报告|故障单|工单|邮件|审查意见|评审意见|"
    r"会议纪要|引用文字)(?:里|中)?\s*"
    r"(?:写着|写道|写的是|说|说道|提到|记录|建议|要求)\s*|"
    r"(?:文章|博客|帖子|日志)(?:里|中)?\s*"
    r"(?:写着|写道|说|说道|提到)\s*|"
    r"(?:issue|ticket|report)\s*(?:里|中)\s*"
    r"(?:写着|写道|提到|记录|建议|要求)\s*|"
    r"(?:引用文字|引用内容|引文)\s*是\s*|"
    r"(?:an?\s+|the\s+)?(?:article|blog|post|document|log|report|ticket|"
    r"issue|email|reviewer(?:\s+comment)?|comment|minutes)\s+"
    r"(?:says?|reads?|states?|mentions?|wrote|suggests?|requests?)\s*|"
    r"\b(?:for\s+example|e\.g\.|example)\s*[:：]?)"
    r"[^.!?;。！？；\n]{0,120}$",
    re.IGNORECASE,
)
_EXPLICIT_EXAMPLE_SUFFIX_RE = re.compile(
    r"^\s*(?:[,，:：—–-]+\s*)?(?:"
    r"(?:只是|仅仅是|仅是|不过是|是(?:一个|个)?|"
    r"(?:is\s+)?(?:just|only)\s+(?:an?\s+)?)"
    r"(?:例子|示例|example)(?:\b|$)|"
    r"(?:是\s*)?(?:什么|啥)\s*意思(?:[？?]|$))",
    re.IGNORECASE,
)
_REPORTED_AUTHORITY_DENIAL_SUFFIX_RE = re.compile(
    r"^\s*(?:(?:skill|技能)\s*)?(?:[,，:：]\s*)?"
    r"(?:我|用户|we|i)?\s*(?:没有|并未|未|不曾)\s*"
    r"(?:授权|允许)(?:你|系统)?\s*(?:调用|使用|加载|应用)?|"
    r"^\s*(?:(?:skill|技能)\s*)?(?:[,，:：]\s*)?"
    r"(?:i|we)\s+(?:did\s+not|didn['’]?t|have\s+not|haven['’]?t)\s+"
    r"(?:authorize|permit)(?:\s+(?:you|the\s+system))?\s+to\s+"
    r"(?:use|load|invoke|apply)",
    re.IGNORECASE,
)
_EXPLICIT_CORRECTION_NEGATION = re.compile(
    r"(?:\bnot|\bwithout|\binstead\s+of|\brather\s+than|\bexcept)"
    r"\s+(?:the\s+)?$|"
    r"(?:不要|不用|不准|不许|勿|没有|不是|而不是|而非|非|排除|除了?)\s*$",
    re.IGNORECASE,
)
_QUOTED_AUTHORITY_RE = re.compile(
    r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\'[^\'\n]*\'(?!\w)|'
    r"`[^`\n]*`"
)
_FENCED_AUTHORITY_RE = re.compile(
    r"(?P<delimiter>`{3,}|~{3,})[\s\S]*?(?P=delimiter)"
)
_UNCLOSED_FENCED_AUTHORITY_RE = re.compile(
    r"(?m)^[ \t]{0,3}(?:`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)"
)
_REPLACEMENT_PREFIX_RE = re.compile(
    r"(?:^|[.!?;,，。！？；—…])\s*(?:actually|no)\s*[,，:]?\s*"
    r"(?=(?:please\s+)?(?:use|load|invoke|apply|follow|select|choose|"
    r"include|switch\s+to)\b)|"
    r"(?:^|[.!?;,，。！？；—…])\s*(?:nah\s+)?scratch\s+that\s*"
    r"(?:[-—,:，]\s*)?(?=(?:please\s+)?(?:use|load|invoke|apply|follow|"
    r"select|choose|include|switch\s+to)\b)|"
    r"(?:^|[.!?;,，。！？；—…])\s*(?:不对|不是这样|改主意了)\s*[,，:]?\s*"
    r"(?=(?:(?:请\s*)?(?:改用|改为|使用|调用|加载|选择|选用)|"
    r"`?[a-z0-9][a-z0-9._/:-]*`?\s*(?:就)?(?:用|使用)\s*"
    r"(?:这个|该)\s*(?:skill|技能)))|"
    r"(?:^|[.!?;。！？；—…])\s*(?=(?:please\s+)?(?:use|load|invoke|apply|"
    r"follow|select|choose|include|switch\s+to)\b"
    r"[^.!?;\n。！？；]{0,120}\binstead\b)|"
    r"(?:^|[.!?;,，。！？；—…])\s*(?=(?:请\s*)?(?:改用|改为))",
    re.IGNORECASE,
)


def _explicit_match_is_negated(
    text: str,
    match_start: int,
    match_end: int | None = None,
) -> bool:
    """Conservatively detect a negation governing an explicit invocation.

    The negator and invocation need not be adjacent (for example, "do not
    ever use" or "不要再使用"). A sentence/semicolon boundary ends its scope;
    an explicit contrast such as "but/而是" starts a new positive clause.
    """
    prefix = text[:match_start]
    boundaries = list(_EXPLICIT_CLAUSE_BOUNDARY.finditer(prefix))
    clause_start = boundaries[-1].end() if boundaries else 0
    relative_match_start = match_start - clause_start
    relative_match_end = max(
        relative_match_start,
        (match_end if match_end is not None else match_start) - clause_start,
    )
    clause_end_match = _EXPLICIT_CLAUSE_BOUNDARY.search(text, match_start)
    clause_end = clause_end_match.start() if clause_end_match else len(text)
    clause = text[clause_start:clause_end]
    if re.search(
        r"\b(?:instead\s+of|rather\s+than)\s*$",
        clause[:relative_match_start],
        re.IGNORECASE,
    ):
        return True
    negations = list(
        effective_negation_matches(clause[:relative_match_start])
    )
    if not negations:
        return False
    latest = negations[-1]
    governed_text = clause[latest.start() : relative_match_end]
    for reset in _EXPLICIT_POSITIVE_RESET.finditer(governed_text):
        # A contrast only resets the prohibition after it has already
        # governed another action ("do not use A, but use B"). Words such as
        # "however", "yet", and "随后" may themselves be modifiers in
        # "do not ... use B" and must not turn a prohibition into authority.
        if (
            _EXPLICIT_SCOPE_ACTION.search(governed_text[: reset.start()])
            and _EXPLICIT_ACTION.search(governed_text[reset.end() :])
        ):
            return False
    for reset in _EXPLICIT_COORDINATE_RESET.finditer(governed_text):
        if (
            _EXPLICIT_SCOPE_ACTION.search(governed_text[: reset.start()])
            and _EXPLICIT_ACTION.search(governed_text[reset.end() :])
        ):
            return False
    return True


def _explicit_match_is_example_mention(
    text: str,
    match_start: int,
    match_end: int,
) -> bool:
    """Return whether invocation-looking syntax is presented as an example."""

    left_boundary = max(
        (
            boundary.end()
            for boundary in _EXPLICIT_CLAUSE_BOUNDARY.finditer(
                text[:match_start]
            )
        ),
        default=0,
    )
    right_boundary = _EXPLICIT_CLAUSE_BOUNDARY.search(text, match_end)
    clause_end = right_boundary.start() if right_boundary else len(text)
    before = text[left_boundary:match_start]
    after = text[match_end:clause_end]
    direct_after = text[match_end : min(len(text), match_end + 160)]
    return bool(
        _EXPLICIT_EXAMPLE_PREFIX_RE.search(before)
        or _EXPLICIT_EXAMPLE_SUFFIX_RE.search(after)
        or _EXPLICIT_EXAMPLE_SUFFIX_RE.search(direct_after)
    )


def _negative_skill_directives(
    text: str,
    identifier_pattern: str,
    *,
    allow_unlabelled: bool = False,
) -> list[tuple[int, int, str]]:
    """Find candidate-specific prohibitions, including coordinated lists."""

    directives: list[tuple[int, int, str]] = []
    for identifier_match in re.finditer(identifier_pattern, text):
        start, end = identifier_match.span()
        left_boundaries = list(
            _EXPLICIT_CLAUSE_BOUNDARY.finditer(text[:start])
        )
        clause_start = left_boundaries[-1].end() if left_boundaries else 0
        right_boundary = _EXPLICIT_CLAUSE_BOUNDARY.search(text, end)
        clause_end = right_boundary.start() if right_boundary else len(text)
        clause = text[clause_start:clause_end]
        relative_start = start - clause_start
        relative_end = end - clause_start

        before = clause[max(0, relative_start - 160) : relative_start]
        after = clause[relative_end : min(len(clause), relative_end + 160)]
        sigiled = bool(re.search(r"\$\s*$", before))
        labelled = bool(
            re.search(r"\bskills?\b", before + after, re.IGNORECASE)
            or "技能" in before + after
        )

        if (
            _EXPLICIT_SKILL_DENIAL_PREFIX_RE.search(before)
            and (labelled or allow_unlabelled)
        ):
            directives.append((start, end, "denied"))
            continue

        if (
            _EXPLICIT_CORRECTION_NEGATION.search(before)
            and (labelled or allow_unlabelled)
        ):
            directives.append((start, end, "denied"))
            continue

        preceding_negations = effective_negation_matches(
            clause[:relative_start]
        )
        if preceding_negations:
            latest = preceding_negations[-1]
            governed = clause[latest.start() : relative_start]
            if (
                (
                    sigiled
                    or (
                        (labelled or allow_unlabelled)
                        and _EXPLICIT_CONTROL_ACTION.search(governed)
                    )
                )
                and _explicit_match_is_negated(text, start, end)
            ):
                directives.append((start, end, "denied"))
                continue

        # Passive forms put the prohibition after the identifier, e.g.
        # "alpha skill should not be used" / "alpha 技能不得使用".
        following = clause[relative_end : min(len(clause), relative_end + 160)]
        following_negations = effective_negation_matches(following)
        passive_prefix = (
            following[: following_negations[0].start()]
            if following_negations
            else ""
        )
        passive_prefix_is_local = bool(
            re.fullmatch(
                r"\s*`?\s*(?:(?:skills?|技能)\s*)?"
                r"(?:(?:is|are|was|were|must|should|can|could|would|shall|"
                r"may|might|被)\s*)?",
                passive_prefix,
                re.IGNORECASE,
            )
        )
        passive_negation = (
            following_negations[0] if following_negations else None
        )
        if (
            (labelled or allow_unlabelled)
            and passive_negation is not None
            and passive_prefix_is_local
            and (
                FORBIDDANCE_MARKER_RE.search(passive_negation.group(0))
                or _EXPLICIT_CONTROL_ACTION.search(
                    following[passive_negation.start() :]
                )
            )
        ):
            directives.append((start, end, "denied"))
    return directives


def _partial_skill_replacement_directives(
    text: str,
    identifier_pattern: str,
    *,
    positive_allowed: bool,
) -> list[tuple[int, int, str]]:
    """Resolve candidate-local replacements without revoking the whole list.

    ``replace beta with gamma`` differs from ``actually, use gamma instead``:
    only beta is superseded, so any independently requested alpha Skill must
    retain its authority.
    """

    identifier_token = r"(?:\$\s*)?`?[a-z0-9][a-z0-9._/:-]*`?"
    quoted_identifier = rf"`?\s*{identifier_pattern}\s*`?"
    patterns = (
        (
            rf"\b(?:replace|substitute)\s+(?:the\s+)?"
            rf"(?P<target>{quoted_identifier})(?:\s+skill)?\s+with\s+"
            rf"(?:the\s+)?{identifier_token}(?:\s+skill)?\b",
            "denied",
        ),
        (
            rf"\b(?:replace|substitute)\s+(?:the\s+)?"
            rf"{identifier_token}(?:\s+skill)?\s+with\s+(?:the\s+)?"
            rf"(?P<target>{quoted_identifier})(?:\s+skill)?\b",
            "requested",
        ),
        (
            rf"\bsubstitute\s+(?:the\s+)?"
            rf"(?P<target>{quoted_identifier})(?:\s+skill)?\s+for\s+"
            rf"(?:the\s+)?{identifier_token}(?:\s+skill)?\b",
            "requested",
        ),
        (
            rf"\bsubstitute\s+(?:the\s+)?"
            rf"{identifier_token}(?:\s+skill)?\s+for\s+(?:the\s+)?"
            rf"(?P<target>{quoted_identifier})(?:\s+skill)?\b",
            "denied",
        ),
        (
            rf"(?:将|把)?\s*(?P<target>{quoted_identifier})\s*(?:技能)?\s*"
            rf"(?:替换为|替换成|换成)\s*{identifier_token}\s*(?:技能)?",
            "denied",
        ),
        (
            rf"(?:将|把)?\s*{identifier_token}\s*(?:技能)?\s*"
            rf"(?:替换为|替换成|换成)\s*"
            rf"(?P<target>{quoted_identifier})\s*(?:技能)?",
            "requested",
        ),
    )
    directives: list[tuple[int, int, str]] = []
    for pattern, directive in patterns:
        if directive == "requested" and not positive_allowed:
            continue
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _explicit_match_is_negated(text, match.start(), match.end()):
                continue
            start, end = match.span("target")
            directives.append((start, end, directive))
    return directives


def _positive_skill_list_directives(
    text: str,
    identifier_pattern: str,
) -> list[tuple[int, int, str]]:
    """Expand one explicit invocation action over a labelled Skill list."""

    directives: list[tuple[int, int, str]] = []
    identifier_token = r"(?:\$\s*)?`?[a-z0-9][a-z0-9._/:-]*`?"
    structured_identifier_token = (
        r"`?[a-z0-9][a-z0-9._/:-]*[-_/:.]"
        r"[a-z0-9._/:-]*`?"
    )
    english_action = (
        r"(?:use|load|invoke|apply|follow|select|choose|include|switch\s+to)"
    )
    english_separator = r"(?:,\s*(?:and\s+)?|\band\b|&)"
    chinese_action = r"(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)"
    chinese_separator = r"(?:，|、|和|及|以及|并且)"
    list_patterns = (
        rf"\b{english_action}\s+(?:both\s+)?(?:the\s+)?"
        rf"(?P<body>{identifier_token}(?:\s*{english_separator}\s*"
        rf"(?:the\s+)?{identifier_token})+)\s+skills?\b",
        rf"\b{english_action}\s+(?:both\s+)?(?:the\s+)?"
        rf"(?P<body>{identifier_token}\s+skill(?:\s*"
        rf"{english_separator}\s*(?:the\s+)?{identifier_token}\s+skill)+)\b",
        rf"{chinese_action}\s*(?P<body>{identifier_token}(?:\s*"
        rf"{chinese_separator}\s*{identifier_token})+)\s*技能",
        rf"{chinese_action}\s*(?P<body>{identifier_token}\s*技能(?:\s*"
        rf"{chinese_separator}\s*{identifier_token}\s*技能)+)",
        rf"\b{english_action}\s+(?:both\s+)?(?:the\s+)?"
        rf"(?P<body>{structured_identifier_token}(?:\s*"
        rf"{english_separator}\s*(?:the\s+)?{structured_identifier_token})+)",
        rf"{chinese_action}\s*(?P<body>{structured_identifier_token}(?:\s*"
        rf"{chinese_separator}\s*{structured_identifier_token})+)",
    )
    for pattern in list_patterns:
        for list_match in re.finditer(pattern, text, re.IGNORECASE):
            body = list_match.group("body")
            body_start = list_match.start("body")
            for local_match in re.finditer(
                identifier_pattern,
                body,
            ):
                if local_match.group(0).strip("`$ ").lower() in {
                    "and",
                    "both",
                    "skill",
                    "the",
                }:
                    continue
                start = body_start + local_match.start()
                end = body_start + local_match.end()
                if _explicit_match_is_negated(text, start, end):
                    continue
                directives.append((start, end, "requested"))
    return directives


def _positive_skill_correction_directives(
    text: str,
    identifier_pattern: str,
) -> list[tuple[int, int, str]]:
    """Keep the named positive side of a labelled list correction."""

    identifier_token = r"(?:\$\s*)?`?[a-z0-9][a-z0-9._/:-]*`?"
    english_action = (
        r"(?:use|load|invoke|apply|follow|select|choose|include|switch\s+to)"
    )
    chinese_action = r"(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)"
    patterns = (
        rf"\b{english_action}\s+(?:the\s+)?(?P<target>"
        rf"{identifier_pattern})\s+(?:and\s+|but\s+)?(?:not|instead\s+of|"
        rf"rather\s+than)\s+(?:the\s+)?{identifier_token}\s+skills?\b",
        rf"{chinese_action}\s*(?P<target>{identifier_pattern})\s*"
        rf"(?:而非|而不是|不是)\s*{identifier_token}\s*技能",
    )
    directives: list[tuple[int, int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            start, end = match.span("target")
            if not _explicit_match_is_negated(text, start, end):
                directives.append((start, end, "requested"))
    return directives


def _resolved_skill_directive(
    intent: ParsedIntent,
    raw_input: str,
    name: str,
    qualified_name: str,
    *,
    allow_bare_reference: bool = True,
) -> str:
    """Resolve current authority plus sticky user-only denial context.

    A current directive always wins. Historical positive requests merely clear
    an older denial; they never grant authority in a later turn.
    """

    replacement_start = _replacement_directive_start(raw_input)
    if replacement_start is None:
        current = _explicit_skill_directive(
            raw_input,
            name,
            qualified_name,
            allow_bare_reference=allow_bare_reference,
        )
    else:
        before = _explicit_skill_directive(
            raw_input[:replacement_start],
            name,
            qualified_name,
            allow_bare_reference=allow_bare_reference,
        )
        after = _explicit_skill_directive(
            raw_input[replacement_start:],
            name,
            qualified_name,
            allow_bare_reference=allow_bare_reference,
        )
        if after != "none":
            current = after
        elif before == "denied" and re.search(
            r"\b(?:use|load|invoke|apply|select|choose)\s+"
            r"(?:it|that(?:\s+skill)?)\b",
            raw_input[replacement_start:],
            re.IGNORECASE,
        ):
            structured = {
                token.lower().strip("`$")
                for token in re.findall(
                    r"(?:\$\s*)?`?[a-z0-9][a-z0-9._/:-]*[-_/:]"
                    r"[a-z0-9._/:-]*`?",
                    raw_input[:replacement_start],
                    re.IGNORECASE,
                )
            }
            normalized_names = {
                _normalize_authority_text(name).strip(),
                _normalize_authority_text(qualified_name).strip(),
            }
            current = (
                "requested"
                if len(structured) == 1 and bool(structured & normalized_names)
                else "none"
            )
        elif before == "requested":
            current = "denied"
        else:
            current = before
    if current != "none":
        return current

    previous = "none"
    for prior_user_turn in tuple(
        getattr(intent, "routing_directive_context", ()) or ()
    ):
        directive = _explicit_skill_directive(
            str(prior_user_turn),
            name,
            qualified_name,
            allow_bare_reference=allow_bare_reference,
        )
        if directive != "none":
            previous = directive
    return "denied" if previous == "denied" else "none"


_AUTHORITY_IDENTIFIER = r"[a-z0-9][a-z0-9._:/-]*"
# Possessive variant for capture patterns: a required trailing literal
# (``skill``) must not pull the identifier boundary back into itself.
# ``使用 code-skills/minicode-study`` otherwise backtracks the identifier
# to ``code-`` so the literal can eat ``skills/``'s prefix, producing a
# bogus unresolved reference that vetoes an otherwise explicit request.
_POSSESSIVE_AUTHORITY_IDENTIFIER = r"[a-z0-9][a-z0-9._:/-]*+"
_EXPLICIT_REFERENCE_PATTERNS = (
    re.compile(
        rf"\$\s*`?({_POSSESSIVE_AUTHORITY_IDENTIFIER})`?", re.IGNORECASE
    ),
    re.compile(
        rf"\b(?:use|using|load|loading|invoke|invoking|apply|applying|"
        rf"follow|following|select|selecting|choose|choosing|include|"
        rf"including|switch(?:ing)?\s+to)\s+(?:the\s+)?skill\s+"
        rf"(?:named\s+|called\s+)`?({_POSSESSIVE_AUTHORITY_IDENTIFIER})`?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:use|using|load|loading|invoke|invoking|apply|applying|"
        rf"follow|following|select|selecting|choose|choosing|include|"
        rf"including|switch(?:ing)?\s+to)\s+(?:the\s+)?(?:skill\s+"
        rf"(?:named\s+|called\s+)?)?`?({_POSSESSIVE_AUTHORITY_IDENTIFIER})`?\s+skill\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)\s*(?:技能\s*"
        rf"(?:名为\s*)?)?`?({_POSSESSIVE_AUTHORITY_IDENTIFIER})`?\s*(?:skill|技能)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:use|using|load|loading|invoke|invoking|apply|applying|"
        rf"follow|following|select|selecting|choose|choosing|include|"
        rf"including|switch(?:ing)?\s+to)\s+(?:the\s+)?`?"
        rf"({_POSSESSIVE_AUTHORITY_IDENTIFIER})`?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:使用|用|调用|加载|应用|按照|选择|选用|改用|改为)\s*`?"
        rf"({_POSSESSIVE_AUTHORITY_IDENTIFIER})`?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b({_POSSESSIVE_AUTHORITY_IDENTIFIER})\b\s*"
        rf"(?:就)?(?:用|使用)\s*(?:这个|该)\s*(?:skill|技能)",
        re.IGNORECASE,
    ),
)
_GLOBAL_SKILL_DENIAL_RE = re.compile(
    r"\b(?:do\s+not|don't|never|without)\s+(?:ever\s+)?"
    # Coordinated verbs ("don't use or load any skill") must not fall
    # through the single-verb form.
    r"(?:use|load|invoke|apply|select|include)"
    r"(?:\s*(?:or|and|nor)\s+(?:use|load|invoke|apply|select|include))*"
    r"\s+(?:any|all|every)\s+skills?\b|"
    r"(?:不要|别|不准|禁止|请勿|勿)\s*(?:再\s*)?(?:实际\s*)?"
    # 同理：'不要加载或使用任何 Skill' 的并列动词组合；动词链整体保持可选，
    # '不要任何技能' 仍可命中。
    r"(?:(?:使用|加载|调用|应用|选择|启用)"
    r"(?:\s*(?:或|和|及|以及)\s*(?:使用|加载|调用|应用|选择|启用))*)?"
    r"\s*(?:任何|所有|全部|一切)\s*(?:skill|技能)",
    re.IGNORECASE,
)


def _active_authority_segment(text: str) -> tuple[str, int]:
    normalized = _mask_quoted_authority_text(
        _normalize_authority_text(text)
    )
    replacement_start = _replacement_directive_start(text)
    if replacement_start is None:
        return normalized, 0
    return normalized[replacement_start:], replacement_start


def _positive_explicit_reference_tokens(text: str) -> tuple[str, ...]:
    """Extract positive explicit identifiers for unresolved-name detection."""
    normalized = _mask_quoted_authority_text(
        _normalize_authority_text(text)
    )
    active, offset = _active_authority_segment(text)
    found: list[tuple[int, str]] = []
    for pattern in _EXPLICIT_REFERENCE_PATTERNS:
        for match in pattern.finditer(active):
            token = str(match.group(1) or "").strip("` ")
            if not token or token.lower() in {
                "called",
                "named",
                "skill",
                "skills",
                "the",
            }:
                continue
            # Unlabelled ordinary words are discussion, not Skill authority.
            matched_text = match.group(0)
            has_label_or_sigil = (
                "$" in matched_text
                or "skill" in matched_text
                or "技能" in matched_text
            )
            if (
                not has_label_or_sigil
                and not _looks_like_unlabelled_skill_identifier(token)
            ):
                continue
            start = offset + match.start()
            end = offset + match.end()
            if _explicit_match_is_example_mention(normalized, start, end):
                continue
            if _explicit_match_is_negated(normalized, start, end):
                continue
            found.append((start, token))
    ordered: list[str] = []
    for _start, token in sorted(found, key=lambda item: item[0]):
        if token not in ordered:
            ordered.append(token)
    return tuple(ordered)


def _resolve_catalog_skill_authority(
    skills: list[dict],
    intent: ParsedIntent,
    *,
    ambiguous_bare_names: set[str],
) -> SkillAuthorityResolution:
    """Resolve authority once against the catalog before semantic scoring."""
    raw_input = str(getattr(intent, "raw_input", "") or "")
    requested: list[str] = []
    denied: list[str] = []
    identifier_owners: dict[str, set[str]] = {}
    context_used = False
    for skill in skills:
        name = str(skill.get("name", ""))
        qualified_name = str(skill.get("qualified_name") or name)
        if not qualified_name:
            continue
        normalized_name = _normalize_authority_text(name).strip()
        normalized_qualified = _normalize_authority_text(qualified_name).strip()
        for identifier in {normalized_name, normalized_qualified}:
            if identifier:
                identifier_owners.setdefault(identifier, set()).add(
                    qualified_name
                )
        allow_bare = normalized_name not in ambiguous_bare_names
        directive = _resolved_skill_directive(
            intent,
            raw_input,
            name,
            qualified_name,
            allow_bare_reference=allow_bare,
        )
        if directive == "requested":
            requested.append(qualified_name)
        elif directive == "denied":
            denied.append(qualified_name)
            if _explicit_skill_directive(
                raw_input,
                name,
                qualified_name,
                allow_bare_reference=allow_bare,
            ) == "none":
                context_used = True

    unresolved: list[str] = []
    ambiguous: list[str] = []
    for token in _positive_explicit_reference_tokens(raw_input):
        owners = identifier_owners.get(
            _normalize_authority_text(token).strip(),
            set(),
        )
        if not owners:
            unresolved.append(token)
        elif len(owners) > 1:
            ambiguous.append(token)

    active, _offset = _active_authority_segment(raw_input)
    deny_all = _GLOBAL_SKILL_DENIAL_RE.search(active) is not None
    return SkillAuthorityResolution(
        requested=tuple(dict.fromkeys(requested)),
        denied=tuple(dict.fromkeys(denied)),
        unresolved=tuple(dict.fromkeys(unresolved)),
        ambiguous=tuple(dict.fromkeys(ambiguous)),
        deny_all=deny_all,
        context_used=context_used,
    )


def _mask_quoted_authority_text(text: str) -> str:
    """Mask quoted examples while preserving offsets for ordered directives."""

    characters = list(text)
    fenced_spans: list[tuple[int, int]] = []
    for match in _FENCED_AUTHORITY_RE.finditer(text):
        fenced_spans.append(match.span())
        characters[match.start() : match.end()] = " " * (
            match.end() - match.start()
        )
    masked_fences = "".join(characters)
    for opening in _UNCLOSED_FENCED_AUTHORITY_RE.finditer(masked_fences):
        fenced_spans.append((opening.start(), len(text)))
        characters[opening.start() :] = " " * (len(text) - opening.start())
    masked_fences = "".join(characters)
    for match in _QUOTED_AUTHORITY_RE.finditer(masked_fences):
        quoted = match.group(0)
        identifier = quoted[1:-1].strip()
        before = masked_fences[max(0, match.start() - 120) : match.start()]
        after = masked_fences[match.end() : min(len(text), match.end() + 32)]
        authority_atom = bool(
            re.fullmatch(_AUTHORITY_IDENTIFIER, identifier, re.IGNORECASE)
            and (
                re.search(
                    r"\bskill\s+(?:named|called)\s*$|技能\s*名为\s*$",
                    before,
                    re.IGNORECASE,
                )
                or re.match(
                    r"\s*(?:(?:skills?|技能)\b|"
                    r"(?:就)?(?:用|使用)\s*(?:这个|该)\s*"
                    r"(?:skills?|技能)\b)",
                    after,
                    re.IGNORECASE,
                )
            )
        )
        if authority_atom:
            # Quotes around the identifier are syntax, not a quoted example.
            # Blank only the delimiters so all following offsets stay stable.
            characters[match.start()] = " "
            characters[match.end() - 1] = " "
            continue
        characters[match.start() : match.end()] = " " * (
            match.end() - match.start()
        )
    return "".join(characters)


def _replacement_directive_start(text: str) -> int | None:
    """Return the start of the final explicit whole-list correction clause."""

    normalized = _mask_quoted_authority_text(
        _normalize_authority_text(text)
    )
    matches = list(_REPLACEMENT_PREFIX_RE.finditer(normalized))
    return matches[-1].end() if matches else None


def _normalize_text(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ")


def _normalize_authority_text(value: str) -> str:
    """Normalize case only; identifier separators remain authority-bearing."""
    return value.lower()


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


def _tool_field(mapping: dict) -> list[str]:
    """Return a stable, unique and bounded routing view of declared tools."""
    tools: list[str] = []
    seen: set[str] = set()
    for tool_name in _list_field(mapping, "tools"):
        if tool_name in seen:
            continue
        seen.add(tool_name)
        tools.append(tool_name)
        if len(tools) >= _MAX_ROUTING_TOOLS:
            break
    return tools


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
