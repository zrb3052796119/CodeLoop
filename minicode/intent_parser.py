"""Intent Parser - Structured user intent parsing layer.

Inspired by: raw material -> clean expression -> task path -> target skill
Transforms user input into stable intent objects before routing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from minicode.logging_config import get_logger

logger = get_logger("intent_parser")


class IntentType(str, Enum):
    CODE = "code"
    DEBUG = "debug"
    REFACTOR = "refactor"
    EXPLAIN = "explain"
    SEARCH = "search"
    REVIEW = "review"
    TEST = "test"
    DOCUMENT = "document"
    CONFIGURE = "configure"
    QUESTION = "question"
    CHAT = "chat"
    MEMORY = "memory"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    ANALYZE = "analyze"
    COMPARE = "compare"
    MERGE = "merge"
    SPLIT = "split"
    MOVE = "move"
    RENAME = "rename"
    UNKNOWN = "unknown"


_CODE_PATTERNS = [
    # A bounded gap (not just \s+) so "write a NEW function"/"the code" still
    # match — a determiner or short adjective between the trigger verb and
    # its target noun is the normal way English phrases these requests.
    (r"\b(?:write|create|implement|add|generate)\b.{0,20}\b(?:functions?|classes?|methods?|modules?|components?|pages?|apis?)\b", IntentType.CODE, ActionType.CREATE),
    (r"\b(?:modify|update|change|fix)\b.{0,20}\b(?:codes?|files?|functions?|classes?|methods?)\b", IntentType.CODE, ActionType.UPDATE),
    (r"\b(?:implement|complete|develop)\b.{0,20}\b(?:features?|tasks?|requirements?)\b", IntentType.CODE, ActionType.CREATE),
]

_DEBUG_PATTERNS = [
    (r"\b(?:debug|fix|solve|resolve|troubleshoot)\b.{0,20}\b(?:errors?|bugs?|issues?|problems?|exceptions?)\b", IntentType.DEBUG, ActionType.ANALYZE),
    (r"(?:what|why)\s+(?:is|does)\s+(?:wrong|error|fail|broken)", IntentType.DEBUG, ActionType.ANALYZE),
    (r"(?:调试|排查|修复|解决).*(?:错误|报错|失败|异常|问题|bug)", IntentType.DEBUG, ActionType.ANALYZE),
]

_REFACTOR_PATTERNS = [
    (r"\b(?:refactor|optimize|improve|clean|simplify|restructure)\b.{0,20}\b(?:codes?|structures?|logic|designs?)\b", IntentType.REFACTOR, ActionType.UPDATE),
]

_EXPLAIN_CONTEXT_TERMS = (
    r"code|file|files|function|method|class|module|architecture|system|project|"
    r"agent|skill|memory|tool|flow|logic|design|routing|pipeline|api|repo|"
    r"repository|codebase|implementation"
)

_EXPLAIN_PATTERNS = [
    # An explain-style verb alone is not enough evidence — "what is the
    # weather" and "tell me a joke" match the bare verb just as well as a
    # real code question. Require a code/project-shaped noun (or a bare
    # filename) nearby so unrelated small talk stays unknown/low-confidence.
    (
        rf"(?:explain|describe|tell|what is|how to|how does)\b.{{0,80}}"
        rf"(?:{_EXPLAIN_CONTEXT_TERMS}|\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:解释|说明|讲解|分析).{0,80}"
        r"(?:代码|架构|流程|调用|工具|系统|项目|技能|记忆|函数|模块|文件|实现|逻辑|设计)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
]

_SEARCH_PATTERNS = [
    # "search FOR the function" is the idiomatic English phrasing — the
    # bounded gap covers the preposition/determiner between verb and noun.
    (r"\b(?:search|find|locate|lookup)\b.{0,20}\b(?:files?|codes?|functions?|classes?|variables?|references?)\b", IntentType.SEARCH, ActionType.READ),
]

_REVIEW_PATTERNS = [
    (
        r"(?:review|check|audit|inspect).{0,80}"
        r"(?:code|file|implementation|design|architecture|system|project|memory|skill|routing)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:审查|检查|审核|评审).{0,80}"
        r"(?:代码|文件|实现|设计|架构|系统|项目|记忆|内存|技能|skill|路由)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
]

_TEST_PATTERNS = [
    (r"\b(?:test|verify|run|execute)\b.{0,20}\b(?:tests?|codes?|programs?|scripts?|cases?)\b", IntentType.TEST, ActionType.EXECUTE),
    (r"(?:测试|验证|运行).*(?:测试|用例|pytest|命令)", IntentType.TEST, ActionType.EXECUTE),
]

_DOCUMENT_PATTERNS = [
    # "document this function" is a common request but had no target noun
    # covering code symbols at all — only docs/comment/README/documentation.
    (r"\b(?:document|comment|write)\b.{0,20}\b(?:docs?|comments?|readme|documentation|functions?|classes?|methods?|code)\b", IntentType.DOCUMENT, ActionType.CREATE),
]

_CONFIGURE_PATTERNS = [
    # Same problem as EXPLAIN: "install" / "init" / "set up" alone match a
    # huge range of everyday sentences ("set up a meeting", "install a new
    # habit"). Require a settings/environment/project-shaped noun nearby.
    (
        r"(?:configure|set\s*up|install|initialize|init)\b.{0,60}"
        r"(?:setting|settings|environment|env|config|dependency|dependencies|"
        r"project|model|server|tool|package|repo|repository|workspace|"
        r"api key|credential)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
]

_MEMORY_PATTERNS = [
    (r"(?:remember|memory|memorize|/memory|# remember)", IntentType.MEMORY, ActionType.CREATE),
]

_SYSTEM_PATTERNS = [
    (r"^(?:/|!)(?:exit|quit|bye|clear|reset|help|settings|config|model|mode)", IntentType.SYSTEM, ActionType.EXECUTE),
]

_ALL_PATTERNS = (
    _SYSTEM_PATTERNS + _REVIEW_PATTERNS + _MEMORY_PATTERNS + _CODE_PATTERNS + _DEBUG_PATTERNS +
    _REFACTOR_PATTERNS + _EXPLAIN_PATTERNS + _SEARCH_PATTERNS +
    _TEST_PATTERNS + _DOCUMENT_PATTERNS + _CONFIGURE_PATTERNS
)

_CHINESE_KEYWORD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("审查", ("audit", "review")),
    ("检查", ("inspect", "review")),
    ("审核", ("audit", "review")),
    ("持久化", ("persistent",)),
    ("记忆", ("memory",)),
    ("内存", ("memory",)),
    ("技能", ("skill",)),
    ("路由", ("routing",)),
    ("自进化", ("evolution",)),
    ("进化", ("evolution",)),
    ("调试", ("debug",)),
    ("测试", ("test",)),
    ("架构", ("architecture",)),
    ("项目", ("project",)),
)


@dataclass
class ParsedIntent:
    raw_input: str
    intent_type: IntentType
    action_type: ActionType
    confidence: float
    entities: dict[str, list[str]] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    complexity_hint: str = "moderate"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_input": self.raw_input,
            "intent_type": self.intent_type.value,
            "action_type": self.action_type.value,
            "confidence": self.confidence,
            "entities": self.entities,
            "keywords": self.keywords,
            "complexity_hint": self.complexity_hint,
            "timestamp": self.timestamp,
        }

    def is_code_related(self) -> bool:
        return self.intent_type in {
            IntentType.CODE, IntentType.DEBUG, IntentType.REFACTOR,
            IntentType.REVIEW, IntentType.TEST,
        }

    def is_read_only(self) -> bool:
        return self.action_type in {ActionType.READ, ActionType.ANALYZE}


class IntentParser:
    def __init__(self):
        self._pattern_cache: list[tuple[re.Pattern, IntentType, ActionType]] = []
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        for pattern, intent, action in _ALL_PATTERNS:
            try:
                self._pattern_cache.append((re.compile(pattern, re.IGNORECASE), intent, action))
            except re.error:
                logger.warning("Invalid pattern: %s", pattern)

    def parse(self, user_input: str) -> ParsedIntent:
        if not user_input or not user_input.strip():
            return ParsedIntent(
                raw_input=user_input,
                intent_type=IntentType.UNKNOWN,
                action_type=ActionType.UNKNOWN,
                confidence=0.0,
            )

        text = user_input.strip()
        intent_type, action_type, match_confidence = self._match_patterns(text)
        entities = self._extract_entities(text)
        keywords = self._extract_keywords(text)
        complexity = self._estimate_complexity(text, intent_type, keywords)
        confidence = self._adjust_confidence(match_confidence, entities, keywords)

        return ParsedIntent(
            raw_input=text,
            intent_type=intent_type,
            action_type=action_type,
            confidence=confidence,
            entities=entities,
            keywords=keywords,
            complexity_hint=complexity,
        )

    def _match_patterns(self, text: str) -> tuple[IntentType, ActionType, float]:
        best_intent = IntentType.UNKNOWN
        best_action = ActionType.UNKNOWN
        best_score = 0.0

        for pattern, intent, action in self._pattern_cache:
            match = pattern.search(text)
            if match:
                score = 1.0 - (match.start() / max(len(text), 1)) * 0.3
                if score > best_score:
                    best_score = score
                    best_intent = intent
                    best_action = action

        return best_intent, best_action, best_score

    def _extract_entities(self, text: str) -> dict[str, list[str]]:
        entities: dict[str, list[str]] = {"files": [], "functions": [], "classes": [], "languages": []}

        file_pattern = re.compile(r"\b([\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))\b", re.I)
        for m in file_pattern.finditer(text):
            if m.group(1) not in entities["files"]:
                entities["files"].append(m.group(1))

        func_pattern = re.compile(r"\b(def|fn|func|function)\s+([\w_]+)\b", re.I)
        for m in func_pattern.finditer(text):
            if m.group(2) not in entities["functions"]:
                entities["functions"].append(m.group(2))

        class_pattern = re.compile(r"\bclass\s+([\w_]+)\b", re.I)
        for m in class_pattern.finditer(text):
            if m.group(1) not in entities["classes"]:
                entities["classes"].append(m.group(1))

        lang_pattern = re.compile(r"\b(python|javascript|typescript|java|go|rust|cpp|c\+\+|react|vue)\b", re.I)
        for m in lang_pattern.finditer(text):
            lang = m.group(1).lower()
            if lang not in entities["languages"]:
                entities["languages"].append(lang)

        return entities

    def _extract_keywords(self, text: str) -> list[str]:
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
                     "have", "has", "had", "do", "does", "did", "will", "would", "could",
                     "should", "may", "might", "must", "can", "need", "to", "of", "in",
                     "for", "on", "with", "at", "by", "from", "as", "into", "through",
                     "during", "before", "after", "above", "below", "between", "under",
                     "again", "further", "then", "once", "here", "there", "when", "where",
                     "why", "how", "all", "any", "both", "each", "few", "more", "most",
                     "other", "some", "such", "no", "nor", "not", "only", "own", "same",
                     "so", "than", "too", "very", "just", "and", "but", "if", "or",
                     "because", "until", "while", "this", "that", "these", "those",
                     "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
                     "she", "her", "it", "its", "they", "them", "their", "what", "which",
                     "who", "whom",
                     # These are the exact trigger verbs the EXPLAIN/CONFIGURE
                     # patterns above require additional context for (see
                     # _EXPLAIN_PATTERNS/_CONFIGURE_PATTERNS). Left unfiltered,
                     # they still leaked out of keyword extraction as an
                     # independent, context-free signal and could route
                     # unrelated small talk through a coincidental keyword
                     # match on some skill's own description/example text.
                     "tell", "describe", "explain", "configure", "setup",
                     "install", "init", "initialize"}
        normalized_text = text.lower()
        words = re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]+", normalized_text)
        keywords = [w for w in words if w not in stopwords and len(w) > 1]
        for chinese_term, aliases in _CHINESE_KEYWORD_ALIASES:
            if chinese_term in normalized_text:
                keywords.extend(aliases)
        seen: set[str] = set()
        unique: list[str] = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return unique[:20]

    def _estimate_complexity(self, text: str, intent: IntentType, keywords: list[str]) -> str:
        length_score = min(len(text) / 200, 1.0)
        intent_scores = {
            IntentType.CODE: 0.6, IntentType.DEBUG: 0.5, IntentType.REFACTOR: 0.7,
            IntentType.EXPLAIN: 0.3, IntentType.SEARCH: 0.2, IntentType.REVIEW: 0.4,
            IntentType.TEST: 0.4, IntentType.DOCUMENT: 0.3, IntentType.CONFIGURE: 0.3,
            IntentType.QUESTION: 0.2, IntentType.CHAT: 0.1, IntentType.MEMORY: 0.1,
            IntentType.SYSTEM: 0.1, IntentType.UNKNOWN: 0.5,
        }
        intent_score = intent_scores.get(intent, 0.5)
        complex_keywords = {"architect", "design", "framework", "system", "platform",
                            "infrastructure", "orchestrate", "pipeline", "migrate",
                            "integrate", "refactor", "optimize", "performance"}
        keyword_score = sum(1 for k in keywords if k in complex_keywords) / max(len(keywords), 1)
        total = length_score * 0.2 + intent_score * 0.5 + keyword_score * 0.3
        if total < 0.3:
            return "simple"
        elif total < 0.6:
            return "moderate"
        return "complex"

    def _adjust_confidence(self, base: float, entities: dict, keywords: list[str]) -> float:
        # Entities/keyword-count are confidence *boosters* for an existing
        # pattern match, not signal on their own. Without this guard, any
        # unmatched (UNKNOWN) message with 3+ incidental keywords or a
        # coincidental file-like token still reported nonzero confidence,
        # undermining "confidence == 0" as a reliable no-signal indicator.
        if base <= 0:
            return 0.0
        confidence = base
        if any(entities.values()):
            confidence += 0.1
        if 3 <= len(keywords) <= 15:
            confidence += 0.05
        return min(1.0, confidence)


_parser: IntentParser | None = None


def get_intent_parser() -> IntentParser:
    global _parser
    if _parser is None:
        _parser = IntentParser()
    return _parser


def parse_intent(user_input: str) -> ParsedIntent:
    return get_intent_parser().parse(user_input)
