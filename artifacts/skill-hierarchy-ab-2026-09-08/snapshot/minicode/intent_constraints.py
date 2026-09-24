"""Typed, deterministic extraction of test-operation constraints.

The interface in this module deliberately describes *constraints*, not
positive operation authority.  In particular, a released constraint means
that a prohibition was explicitly denied; it never means that the user asked
the agent to perform the operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "ConstraintDiscourse",
    "TestConstraint",
    "TestConstraintAction",
    "TestConstraintScope",
    "TestConstraintState",
    "extract_test_constraints",
]


class TestConstraintAction(str, Enum):
    """Test operation controlled by a constraint."""

    CREATE = "create"
    EXECUTE = "execute"


class TestConstraintState(str, Enum):
    """Whether a test-operation constraint is active or explicitly lifted."""

    DENIED = "denied"
    RELEASED = "released"


class TestConstraintScope(str, Enum):
    """Whether a constraint applies to all tests or named test subtypes."""

    GLOBAL = "global"
    SUBTYPE = "subtype"


class ConstraintDiscourse(str, Enum):
    """How the constraint-shaped language functions in the utterance."""

    DIRECTIVE = "directive"
    META = "meta"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class TestConstraint:
    """One immutable, source-grounded test-operation constraint."""

    action: TestConstraintAction
    state: TestConstraintState
    scope: TestConstraintScope
    subtypes: tuple[str, ...]
    span: tuple[int, int]
    text: str
    evidence_family: str
    discourse: ConstraintDiscourse


# Pytest otherwise mistakes these public domain types for test containers when
# a consumer imports them directly into a test module.
TestConstraintAction.__test__ = False
TestConstraintState.__test__ = False
TestConstraintScope.__test__ = False
TestConstraint.__test__ = False


@dataclass(frozen=True, slots=True)
class _TestReference:
    start: int
    end: int
    subtypes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ActionMention:
    action: TestConstraintAction
    start: int
    end: int
    family: str


@dataclass(frozen=True, slots=True)
class _StateEvidence:
    state: TestConstraintState
    family: str
    span: tuple[int, int]


_ENGLISH_SUBTYPE_ALIASES: Final[tuple[tuple[str, str], ...]] = (
    (r"end[- ]to[- ]end|e2e", "e2e"),
    (r"regression", "regression"),
    (r"unit", "unit"),
    (r"integration", "integration"),
    (r"browser", "browser"),
    (r"performance|perf", "performance"),
    (r"slow", "slow"),
    (r"smoke", "smoke"),
    (r"contract", "contract"),
    (r"load|stress", "load"),
    (r"security", "security"),
    (r"acceptance", "acceptance"),
    (r"functional", "functional"),
    (r"system", "system"),
    (r"windows|win32", "windows"),
    (r"linux", "linux"),
    (r"macos|os\s*x", "macos"),
    (r"android", "android"),
    (r"ios", "ios"),
)
_CHINESE_SUBTYPE_ALIASES: Final[tuple[tuple[str, str], ...]] = (
    (r"端到端", "e2e"),
    (r"回归", "regression"),
    (r"单元", "unit"),
    (r"集成", "integration"),
    (r"浏览器", "browser"),
    (r"性能", "performance"),
    (r"慢速|耗时", "slow"),
    (r"冒烟", "smoke"),
    (r"契约", "contract"),
    (r"负载|压力", "load"),
    (r"安全", "security"),
    (r"验收", "acceptance"),
    (r"功能", "functional"),
    (r"系统", "system"),
)

_ENGLISH_SUBTYPE_PATTERN = "(?:" + "|".join(
    pattern for pattern, _canonical in _ENGLISH_SUBTYPE_ALIASES
) + ")"
_CHINESE_SUBTYPE_PATTERN = "(?:" + "|".join(
    pattern for pattern, _canonical in _CHINESE_SUBTYPE_ALIASES
) + ")"

_TEST_REFERENCE_RE = re.compile(
    rf"\b(?:"
    rf"(?:(?:{_ENGLISH_SUBTYPE_PATTERN})"
    rf"(?:\s*(?:,|/|and|or)\s*{_ENGLISH_SUBTYPE_PATTERN})*\s+)"
    rf"(?:(?:test\s+)?(?:cases?|suites?)|tests?)|"
    rf"test\s+(?:cases?|suites?|files?|code)|"
    rf"tests?|pytest|testing|suites?"
    rf")\b|"
    rf"(?:(?:{_CHINESE_SUBTYPE_PATTERN})(?:和|与|、|及)?)*"
    rf"(?:测试用例|测试套件|测试文件|测试代码|测试|单测|测例|用例|复测|跑测)",
    re.IGNORECASE,
)

_BARE_CASE_REFERENCE_RE = re.compile(r"\bcases?\b", re.IGNORECASE)
_BARE_CASE_TEST_ACTION_RE = re.compile(
    r"\b(?:creat(?:e|es|ed|ing)|add(?:s|ed|ing)?|writ(?:e|es|ing|ten)|"
    r"author(?:s|ed|ing)?)\b|(?:生成|创建|新增|添加|编写|补)",
    re.IGNORECASE,
)

_PLATFORM_QUALIFIER_RE = re.compile(
    r"^\s*(?:on|under|for|in)\s+(?:the\s+)?"
    r"(?P<platform>windows|win32|linux|macos|os\s*x|android|ios)\b",
    re.IGNORECASE,
)

_CREATE_ACTION_RE = re.compile(
    r"\b(?:creat(?:e|es|ed|ing|ion)|uncreated|"
    r"generat(?:e|es|ed|ing|ion)|add(?:s|ed|ing|itions?)?|"
    r"edit(?:s|ed|ing)?|"
    r"writ(?:e|es|ing|ten)|author(?:s|ed|ing|ship)?|"
    r"modif(?:y|ies|ied|ying|ications?)|chang(?:e|es|ed|ing)|"
    r"touch(?:es|ed|ing)?|unwritten|untouched|unchanged|unmodified|"
    r"intact|alone)\b|"
    r"(?:生成|新建|创建|新增|添加|增加|编写|写|加|添|补充|补|修改|改动|改|变更|"
    r"原样|不变|未写|没写|(?<!启)动)",
    re.IGNORECASE,
)
_EXECUTE_ACTION_RE = re.compile(
    r"\b(?:run|runs|running|ran|execut(?:e|es|ed|ing|ion)|"
    r"start(?:s|ed|ing)?|launch(?:es|ed|ing)?|re[- ]?test(?:s|ed|ing)?|"
    r"skip(?:s|ped|ping)?|omit(?:s|ted|ting)?|idle|unrun|unexecuted)\b|"
    r"(?:运行|执行|跑测|复测|跑|启动|跳过|略过|避开|未执行|未运行|未跑)",
    re.IGNORECASE,
)

_ARTIFACT_RE = re.compile(
    r"\btest(?:[-\s]+(?:run|execution))?[-\s]+"
    r"(?:reports?|results?|summar(?:y|ies)|documentation|docs?|"
    r"failures?|outputs?|logs?|sections?|headings?|labels?|wording|"
    r"phrases?|strings?)\b|"
    r"\btest[-\s]+results?\s+sections?\b|"
    r"(?:测试报告|测试结果|测试摘要|测试文档|测试说明|测试输出|"
    r"测试日志|测试失败(?:项|记录)?|测试标题|测试措辞)",
    re.IGNORECASE,
)
_NON_TEST_MUTATION_OBJECT_RE = re.compile(
    r"\b(?:source\s+code|code|implementation|behaviou?r|config|"
    r"documentation|docs?|readme)\b|(?:源代码|源码|代码|实现|行为|配置|文档|说明)",
    re.IGNORECASE,
)
_INTERVENING_NON_TEST_INTENT_RE = re.compile(
    r"\b(?:logs?|traceback|report|read|inspect|review|analy[sz]e)\b|"
    r"(?:日志|堆栈|报告|只看|查看|阅读|分析|排查)",
    re.IGNORECASE,
)

_QUOTE_RE = re.compile(
    r'`(?P<backtick>[^`\n]+)`|'
    r'"(?P<double>[^"\n]+)"|'
    r"“(?P<cjk_double>[^”\n]+)”|"
    r"‘(?P<cjk_single>[^’\n]+)’"
)
_CLAUSE_BOUNDARY_RE = re.compile(
    r"[;；.!?。！？,，\n]+|\b(?:but|however)\b|(?:但是|不过|而是)",
    re.IGNORECASE,
)

_META_DISCOURSE_RE = re.compile(
    r"^\s*(?:"
    r"what\s+does\b.*\bmean|"
    r"(?:explain|review|quote|rename|compare|translate|define|discuss)\b"
    r".*\b(?:string|wording|phrase|heading|label|sentence|term|words?|"
    r"meaning)|"
    r"report\s+(?:how\s+many|whether|why|what)|"
    r"(?:the\s+)?(?:[\w'-]+\s+){0,3}(?:author|report|"
    r"note|guide|label)\s+(?:says?|states?|reads?|contains?)\b|"
    r"(?:解释|说明|引用|比较|对比|重命名|改名).{0,36}"
    r"(?:字符串|措辞|短语|标题|标签|句子|术语|含义)|"
    r"报告(?:有多少|多少|是否|为何|为什么)"
    r")",
    re.IGNORECASE,
)
_STATUS_DISCOURSE_RE = re.compile(
    r"\b(?:tests?|pytest)\b.{0,28}\b(?:was|were|did|had|have|has)\s+not\b"
    r".{0,24}\b(?:run|executed|started|launched)\b|"
    r"\b(?:tests?|pytest)\b.{0,20}\b(?:wasn['’]?t|weren['’]?t|didn['’]?t|"
    r"hadn['’]?t|haven['’]?t|hasn['’]?t)\b.{0,24}"
    r"\b(?:run|execute|executed|start|started)\b|"
    r"\b(?:tests?|test\s+files?|pytest)\b.{0,20}"
    r"\b(?:is|are|was|were)\s+(?:currently\s+)?"
    r"(?:unrun|unexecuted|skipped|unchanged|unmodified|untouched)\b|"
    r"\b(?:tests?|pytest|test\s+job)\b.{0,24}\b(?:was|were)\s+never\s+"
    r"(?:run|executed|started|launched)\b|"
    r"\b(?:tests?|pytest|test\s+job)\b.{0,24}\bnever\s+(?:got\s+)?"
    r"(?:run|running|executed|started|launched)\b|"
    r"\b(?:ci|pipeline|test)?\s*job\b.{0,24}\bcould\s+not\s+"
    r"(?:run|execute|start|launch)\b.{0,20}\b(?:tests?|pytest)\b|"
    r"\b(?:yesterday|previously|earlier|historically|last\s+(?:run|"
    r"night|week)|at\s+the\s+time)\b.{0,56}\b(?:tests?|pytest|"
    r"testing)\b|"
    r"(?:测试|用例|pytest).{0,20}(?:尚未|未曾|此前未|之前未|昨天没|"
    r"昨日没|上次没|没有执行过|没有运行过|未(?:执行|运行|跑))",
    re.IGNORECASE,
)
_DIRECTIVE_DISCOURSE_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|must(?:n['’]?t|\s+not)|"
    r"should(?:n['’]?t|\s+not)|may\s+not|shall\s+not|avoid|refrain|"
    r"leave|keep)\b|"
    r"(?:不要|别|严禁|禁止|不准|不许|不允许|不得|请勿|切勿|勿|避免|"
    r"保持|维持|必须)",
    re.IGNORECASE,
)
_PAST_NEGATIVE_RE = re.compile(
    r"\b(?:was|were|did|had|have|has)\s+not\b|"
    r"\b(?:wasn['’]?t|weren['’]?t|didn['’]?t|hadn['’]?t|"
    r"haven['’]?t|hasn['’]?t)\b|"
    r"(?:尚未|未曾|此前未|之前未|昨天没|昨日没|上次没|没有执行过|"
    r"没有运行过|未(?:执行|运行|跑))",
    re.IGNORECASE,
)

_RELEASE_FORBIDDANCE_RE = re.compile(
    r"\b(?:not|no\s+longer)\s+(?:forbidden|prohibited|banned|"
    r"disallowed|off[- ]limits?)\b|"
    r"\b(?:isn|aren)['’]?t\s+(?:forbidden|prohibited|banned|"
    r"disallowed|off[- ]limits?)\b|"
    r"\b(?:false|untrue)\s+that\b.{0,48}\b(?:forbidden|prohibited|"
    r"off[- ]limits?)\b|"
    r"(?:并非|不是|不)\s*(?:被)?(?:禁止|严禁|不准|不许|不允许)",
    re.IGNORECASE,
)
_PERMISSION_RELEASE_RE = re.compile(
    r"\b(?:is|are|be|being)\s+(?:now\s+)?(?:allowed|permitted)\b|"
    r"(?<!不)(?<!不被)(?:允许|准许|可以)\s*"
    r"(?:创建|新增|添加|编写|写|运行|执行|跑|测试)",
    re.IGNORECASE,
)
_OUTER_NEGATED_SKIP_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|never|cannot|can['’]?t|must\s+not|"
    r"should\s+not)\s+(?:ever\s+)?(?:skip|omit|avoid)\b|"
    # ``(不要|别)忘了跑`` negates the skipping itself: the operation is
    # affirmed through the double negation.
    r"(?:不要|别|不能|不可|不应|不准|不许)\s*(?:再)?"
    r"(?:跳过|略过|避开|拒绝\s*(?:跑|运行|执行|复测|写|创建|新增|添加|补)|"
    r"(?:忘记|忘了)\s*(?:去)?\s*"
    r"(?:跑|运行|执行|复测|写|创建|新增|添加|补)|"
    r"不\s*(?:跑|运行|执行|复测|写|创建|新增|添加|补))|"
    r"\b(?:cannot|can['’]?t|must\s+not)\s+leave\b.{0,48}"
    r"\b(?:unrun|unexecuted|unwritten|uncreated)\b|"
    r"\bnot\s+(?:asking|telling)\s+(?:you\s+)?(?:not\s+to|to\s+avoid)\b|"
    r"\b(?:it\s+)?(?:is\s+not|isn['’]?t)\s+true\s+that\b.{0,64}"
    r"\b(?:should|must|may|shall)\s+not\b|"
    r"(?:不是|并非)\s*(?:让|叫|要)\s*(?:你)?\s*"
    r"(?:不要|别|不准|不许)",
    re.IGNORECASE,
)
_NEGATED_NEGATIVE_STATE_RE = re.compile(
    r"\bnot\s+(?:unrun|unexecuted|unwritten|untouched)\b|"
    r"(?:并非|不是)\s*(?:未执行|未运行|未写|没写|原样|不变)",
    re.IGNORECASE,
)

_POST_FORBIDDANCE_RE = re.compile(
    r"\b(?:forbidden|prohibited|banned|disallowed|off[- ]limits?|"
    r"not\s+(?:allowed|permitted)|"
    r"(?:isn|aren)['’]?t\s+(?:allowed|permitted)|"
    # Bare ``without tests`` carries no action verb for the prefix path to
    # negate; the trailing guard leaves ``without test execution`` and
    # friends on their existing prefix-denial reading.
    r"without\s+(?:any\s+|all\s+)?tests?"
    r"(?!\s*(?:execution|creation|authoring|runs?|files?|cases?|suites?|"
    r"code)))\b|"
    r"(?:禁止|严禁|取消|不允许|不需要(?:再)?(?:做|进行)?|无需(?:再)?(?:做|进行)?|"
    # Postposed denials (``复测不用做``/``复测就不要再做了``).  The explicit
    # 做/进行 tail keeps prefix forms with a concrete verb (``不用补``、
    # ``不要跑``) on the negated-action path unchanged.
    r"(?:就|也|都)?(?:不要|不用|别)(?:再)?(?:做|进行))",
    re.IGNORECASE,
)
_CREATE_NEGATIVE_QUANTITY_RE = re.compile(
    r"\bno\s+(?:new|additional)\s+tests?\b|"
    r"\bno\s+test\s+(?:edits?|changes?|modifications?)\b|"
    r"\b(?:no|zero)\s+(?:(?:additional|new|existing|any)\s+)*"
    r"(?:test\s+(?:cases?|additions?|creation|authoring|code|files?)|"
    r"tests?|changes?|modifications?|edits?)\b.{0,28}"
    r"\b(?:creat|add|writ|author|modif|chang|touch)?\w*\b|"
    r"\b(?:creat|add|writ|author|modif|chang|touch)\w*\b.{0,18}"
    r"\b(?:no|zero)\b.{0,12}\b(?:tests?|test\s+cases?)\b|"
    r"(?:一(?:条|行|个|字)?|零(?:条|行|个)?)"
    r"[^,，;；。.!?]{0,18}(?:测试|用例)"
    r"[^,，;；。.!?]{0,12}(?:不|别|没)"
    r"(?:创建|新增|添加|增加|编写|写|加|补|修改|改|动)|"
    r"(?:不需要|无需)\s*(?:任何|新的|新)?\s*(?:测试用例|用例|单测)|"
    r"(?:测试|用例|测试代码|测试文件)"
    r"[^,，;；。.!?]{0,18}(?:一(?:条|行|个|字)?|零(?:条|行|个)?)"
    r"[^,，;；。.!?]{0,10}(?:不|别|没)"
    r"(?:创建|新增|添加|增加|编写|写|加|补|修改|改|动)",
    re.IGNORECASE,
)
_EXECUTE_NEGATIVE_QUANTITY_RE = re.compile(
    r"\b(?:no|zero)\s+(?:(?:test|pytest)\s+)?(?:runs?|executions?)\b|"
    r"^\s*(?:no|zero)\s+tests?\b.{0,28}"
    r"\b(?:run|execut|start|launch)\w*\b|"
    r"\b(?:run|execut|start|launch)\w*\b.{0,14}"
    r"\b(?:no|zero|none)\b|"
    r"(?:一(?:次|个)?|零(?:次|个)?)"
    r"[^,，;；。.!?]{0,18}(?:测试|pytest)?"
    r"[^,，;；。.!?]{0,12}(?:不|别|没)"
    r"(?:运行|执行|跑|启动|复测)|"
    r"(?:测试|用例|pytest)"
    r"[^,，;；。.!?]{0,18}(?:一(?:次|个)?|零(?:次|个)?)"
    r"[^,，;；。.!?]{0,10}(?:不|别|没)"
    r"(?:运行|执行|跑|启动|复测)",
    re.IGNORECASE,
)
_PREFIX_DENIAL_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|never|must\s+not|should\s+not|"
    r"mustn['’]?t|shouldn['’]?t|may\s+not|shall\s+not|cannot|"
    r"can['’]?t|"
    r"under\s+no\s+circumstances|in\s+no\s+circumstances|"
    r"avoid(?:ing)?(?:\s+both)?|refrain\s+from|omit|"
    r"without\s+(?=(?:any\s+)?(?:creat|add|writ|author|run|execut|"
    r"test(?:\s+(?:creation|execution|authoring|runs?))?)))\b|"
    r"\bneither\b|"
    r"(?:不用|不要|别|严禁|禁止|不准|不许|不允许|不得|请勿|切勿|勿|避免|"
    r"取消|既不|也不|都不|一律不|保持不|维持不)",
    re.IGNORECASE,
)
_DIRECT_NOT_BEFORE_ACTION_RE = re.compile(
    r"\bnot\s+(?=(?:be\s+)?(?:creat|add|writ|author|modif|chang|"
    r"touch|run|execut|start|launch|re[- ]?test))|"
    r"不\s*(?=(?:创建|新增|添加|增加|编写|写|加|补|修改|改|变更|"
    r"运行|执行|跑|启动|复测))",
    re.IGNORECASE,
)
_RELATIVE_HISTORICAL_NEGATION_RE = re.compile(
    r"^\s*(?:that|which|who)\b.{0,40}"
    r"(?:\b(?:did|was|were|had|could)\s+not\b|"
    r"\b(?:didn|wasn|weren|hadn|couldn)['’]?t\b)",
    re.IGNORECASE,
)

_INTRINSIC_SKIP_FAMILIES: Final[frozenset[str]] = frozenset(
    {"skip", "omit"}
)
_INTRINSIC_NEGATIVE_STATE_FAMILIES: Final[frozenset[str]] = frozenset(
    {"unexecuted", "unwritten", "idle", "uncreated"}
)


def _mask_spans(text: str, spans: tuple[tuple[int, int], ...]) -> str:
    if not spans:
        return text
    characters = list(text)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _quote_content_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for match in _QUOTE_RE.finditer(text):
        for group_name in ("backtick", "double", "cjk_double", "cjk_single"):
            if match.group(group_name) is not None:
                spans.append(match.span(group_name))
                break
    return tuple(spans)


def _clause_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for boundary in _CLAUSE_BOUNDARY_RE.finditer(text):
        span = _trimmed_span(text, cursor, boundary.start())
        if span is not None:
            spans.append(span)
        cursor = boundary.end()
    span = _trimmed_span(text, cursor, len(text))
    if span is not None:
        spans.append(span)
    return tuple(spans)


def _canonical_subtypes(value: str) -> tuple[str, ...]:
    found: list[tuple[int, str]] = []
    aliases = (*_ENGLISH_SUBTYPE_ALIASES, *_CHINESE_SUBTYPE_ALIASES)
    for pattern, canonical in aliases:
        for match in re.finditer(pattern, value, re.IGNORECASE):
            found.append((match.start(), canonical))
    found.sort()
    result: list[str] = []
    for _position, canonical in found:
        if canonical not in result:
            result.append(canonical)
    return tuple(result)


def _find_references(
    text: str,
    *,
    allow_bare_case: bool = False,
) -> tuple[_TestReference, ...]:
    references: list[_TestReference] = []
    for match in _TEST_REFERENCE_RE.finditer(text):
        end = match.end()
        qualifier = _PLATFORM_QUALIFIER_RE.match(text[end : end + 40])
        reference_text = match.group(0)
        if qualifier is not None:
            end += qualifier.end()
            reference_text = text[match.start() : end]
        references.append(
            _TestReference(
                start=match.start(),
                end=end,
                subtypes=_canonical_subtypes(reference_text),
            )
        )
    if allow_bare_case and _BARE_CASE_TEST_ACTION_RE.search(text):
        occupied = tuple((item.start, item.end) for item in references)
        for match in _BARE_CASE_REFERENCE_RE.finditer(text):
            if any(
                start < match.end() and match.start() < end
                for start, end in occupied
            ):
                continue
            references.append(
                _TestReference(
                    start=match.start(),
                    end=match.end(),
                    subtypes=(),
                )
            )
    references.sort(key=lambda item: (item.start, item.end))
    return tuple(references)


def _action_family(value: str, action: TestConstraintAction) -> str:
    lowered = value.casefold().replace(" ", "").replace("-", "")
    if action is TestConstraintAction.EXECUTE:
        if re.fullmatch(r"skip(?:s|ped|ping)?|跳过|略过|避开", lowered):
            return "skip"
        if re.fullmatch(r"omit(?:s|ted|ting)?", lowered):
            return "omit"
        if lowered in {
            "idle",
            "unrun",
            "unexecuted",
            "未执行",
            "未运行",
            "未跑",
        }:
            if lowered == "idle":
                return "idle"
            return "unexecuted"
        return "execute"
    if lowered in {
        "uncreated",
        "unwritten",
        "untouched",
        "unchanged",
        "unmodified",
        "intact",
        "alone",
        "未写",
        "没写",
        "原样",
        "不变",
    }:
        return "uncreated" if lowered == "uncreated" else "unwritten"
    if re.fullmatch(
        r"modif.*|chang.*|touch.*|修改|改动|改|变更|动",
        lowered,
    ):
        return "mutation"
    return "create"


def _find_action_mentions(text: str) -> tuple[_ActionMention, ...]:
    mentions: list[_ActionMention] = []
    for action, pattern in (
        (TestConstraintAction.CREATE, _CREATE_ACTION_RE),
        (TestConstraintAction.EXECUTE, _EXECUTE_ACTION_RE),
    ):
        for match in pattern.finditer(text):
            mentions.append(
                _ActionMention(
                    action=action,
                    start=match.start(),
                    end=match.end(),
                    family=_action_family(match.group(0), action),
                )
            )
    mentions.sort(key=lambda item: (item.start, item.end, item.action.value))

    # ``omit test authoring`` and ``omit test execution`` use *omit* as the
    # denial predicate.  The explicit operation noun carries the action; a
    # second synthetic EXECUTE constraint would be incorrect.
    filtered: list[_ActionMention] = []
    for mention in mentions:
        if mention.family == "omit" and any(
            other is not mention
            and other.family != "omit"
            and abs(other.start - mention.end) <= 40
            for other in mentions
        ):
            continue
        filtered.append(mention)
    return tuple(filtered)


def _gap(text: str, mention: _ActionMention, reference: _TestReference) -> str:
    if mention.end <= reference.start:
        return text[mention.end : reference.start]
    if reference.end <= mention.start:
        return text[reference.end : mention.start]
    return ""


def _reference_for_mention(
    text: str,
    mention: _ActionMention,
    references: tuple[_TestReference, ...],
) -> _TestReference | None:
    ranked: list[tuple[int, int, _TestReference]] = []
    for reference in references:
        gap = _gap(text, mention, reference)
        if len(gap) > 64 or re.search(r"[;；.!?。！？\n]", gap):
            continue
        if re.search(r"\b(?:documentation|docs?|report|results?|summary|"
                     r"failure|policy)\b", gap, re.IGNORECASE):
            continue
        if re.search(
            r"\b(?:says?|states?|reports?|notes?)\b",
            gap,
            re.IGNORECASE,
        ):
            continue
        if (
            mention.family == "mutation"
            and reference.end <= mention.start
            and re.search(r"[,，]", gap)
        ):
            continue
        if mention.action is TestConstraintAction.CREATE:
            if reference.start >= mention.end:
                object_context = text[max(0, mention.start - 24) : mention.start]
            else:
                object_context = text[reference.end : mention.start]
            if _NON_TEST_MUTATION_OBJECT_RE.search(object_context):
                continue
        distance = len(gap)
        direction_penalty = 0 if mention.end <= reference.start else 2
        ranked.append((distance, direction_penalty, reference))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1], item[2].start))
    return ranked[0][2]


def _classify_discourse(
    text: str,
    *,
    forced: ConstraintDiscourse | None,
) -> ConstraintDiscourse:
    if forced is not None:
        return forced
    if _META_DISCOURSE_RE.search(text):
        return ConstraintDiscourse.META
    if (
        _STATUS_DISCOURSE_RE.search(text)
        and not _DIRECTIVE_DISCOURSE_RE.search(text)
    ):
        return ConstraintDiscourse.STATUS
    return ConstraintDiscourse.DIRECTIVE


def _negative_quantity_match(
    text: str,
    action: TestConstraintAction,
) -> re.Match[str] | None:
    pattern = (
        _CREATE_NEGATIVE_QUANTITY_RE
        if action is TestConstraintAction.CREATE
        else _EXECUTE_NEGATIVE_QUANTITY_RE
    )
    return pattern.search(text)


def _has_negative_quantity(
    text: str,
    action: TestConstraintAction,
) -> bool:
    return _negative_quantity_match(text, action) is not None


def _state_for_mention(
    text: str,
    mention: _ActionMention,
    reference: _TestReference,
    discourse: ConstraintDiscourse,
    all_mentions: tuple[_ActionMention, ...],
    references: tuple[_TestReference, ...],
) -> _StateEvidence | None:
    if (
        reference.end <= mention.start
        and _RELATIVE_HISTORICAL_NEGATION_RE.search(
            text[reference.end : mention.start]
        )
    ):
        # ``Start the tests that the pipeline did not launch`` is a positive
        # command whose relative clause reports an earlier failure.  The
        # embedded past negation must not become a current prohibition.
        return None

    pair_start = min(mention.start, reference.start)
    pair_end = max(mention.end, reference.end)
    local_start = max(0, pair_start - 64)
    local_end = min(len(text), pair_end + 64)
    local = text[local_start:local_end]

    release = _RELEASE_FORBIDDANCE_RE.search(local)
    if release is not None:
        return _StateEvidence(
            TestConstraintState.RELEASED,
            "double-negation",
            (local_start + release.start(), local_start + release.end()),
        )
    negated_state = _NEGATED_NEGATIVE_STATE_RE.search(local)
    if negated_state is not None:
        return _StateEvidence(
            TestConstraintState.RELEASED,
            "double-negation",
            (
                local_start + negated_state.start(),
                local_start + negated_state.end(),
            ),
        )
    negative_quantity = _negative_quantity_match(local, mention.action)
    if negative_quantity is not None:
        # A zero allowance is still a denial, despite the word ``allowed``.
        return _StateEvidence(
            TestConstraintState.DENIED,
            "negative-quantity",
            (
                local_start + negative_quantity.start(),
                local_start + negative_quantity.end(),
            ),
        )
    outer_release = _OUTER_NEGATED_SKIP_RE.search(local)
    if outer_release is not None:
        return _StateEvidence(
            TestConstraintState.RELEASED,
            "double-negation",
            (
                local_start + outer_release.start(),
                local_start + outer_release.end(),
            ),
        )
    permission_release = _PERMISSION_RELEASE_RE.search(local)
    if permission_release is not None:
        return _StateEvidence(
            TestConstraintState.RELEASED,
            "permission-release",
            (
                local_start + permission_release.start(),
                local_start + permission_release.end(),
            ),
        )

    past_negative = _PAST_NEGATIVE_RE.search(local)
    if (
        discourse in {ConstraintDiscourse.META, ConstraintDiscourse.STATUS}
        and past_negative is not None
    ):
        return _StateEvidence(
            TestConstraintState.DENIED,
            "historical-status"
            if discourse is ConstraintDiscourse.STATUS
            else "reported-negation",
            (
                local_start + past_negative.start(),
                local_start + past_negative.end(),
            ),
        )

    if mention.family in _INTRINSIC_SKIP_FAMILIES:
        return _StateEvidence(
            TestConstraintState.DENIED,
            "omission-directive",
            (mention.start, mention.end),
        )
    if mention.family in _INTRINSIC_NEGATIVE_STATE_FAMILIES:
        return _StateEvidence(
            TestConstraintState.DENIED,
            "unchanged-state",
            (mention.start, mention.end),
        )

    post = _POST_FORBIDDANCE_RE.search(local)
    if post is not None:
        return _StateEvidence(
            TestConstraintState.DENIED,
            "forbidden-predicate",
            (local_start + post.start(), local_start + post.end()),
        )

    prefix_matches = tuple(
        _PREFIX_DENIAL_RE.finditer(text, local_start, pair_end)
    )
    direct_not_matches = tuple(
        _DIRECT_NOT_BEFORE_ACTION_RE.finditer(text, local_start, pair_end)
    )
    if prefix_matches or direct_not_matches:
        marker = max(
            (*prefix_matches, *direct_not_matches),
            key=lambda item: item.start(),
        )
        intervening_mentions = tuple(
            other
            for other in all_mentions
            if other != mention
            and marker.end() <= other.start < mention.start
        )
        if any(
            _reference_for_mention(text, other, references) is None
            for other in intervening_mentions
        ):
            return None
        family = (
            "omission-directive"
            if re.search(
                r"\b(?:avoid(?:ing)?|refrain|omit|without)\b|(?:避免)",
                marker.group(0),
                re.IGNORECASE,
            )
            else "negated-action"
        )
        return _StateEvidence(
            TestConstraintState.DENIED,
            family,
            marker.span(),
        )
    return None


def _synthetic_generic_mentions(
    text: str,
    references: tuple[_TestReference, ...],
) -> tuple[_ActionMention, ...]:
    if not references:
        return ()
    reference = references[0]
    quantity_mentions: list[_ActionMention] = []
    for action in (TestConstraintAction.CREATE, TestConstraintAction.EXECUTE):
        if _has_negative_quantity(text, action):
            quantity_mentions.append(
                _ActionMention(
                    action=action,
                    start=reference.start,
                    end=reference.end,
                    family="create" if action is TestConstraintAction.CREATE else "execute",
                )
            )
    if quantity_mentions:
        return tuple(quantity_mentions)
    if not (
        _POST_FORBIDDANCE_RE.search(text)
        or _RELEASE_FORBIDDANCE_RE.search(text)
        or _PERMISSION_RELEASE_RE.search(text)
    ):
        return ()
    return (
        _ActionMention(
            action=TestConstraintAction.EXECUTE,
            start=reference.start,
            end=reference.end,
            family="execute",
        ),
    )


def _extract_clause(
    original: str,
    masked: str,
    *,
    absolute_start: int,
    forced_discourse: ConstraintDiscourse | None,
    allow_bare_case: bool = False,
) -> tuple[TestConstraint, ...]:
    artifact_spans = tuple(match.span() for match in _ARTIFACT_RE.finditer(masked))
    semantic_text = _mask_spans(masked, artifact_spans)
    references = _find_references(
        semantic_text,
        allow_bare_case=allow_bare_case,
    )
    if not references:
        return ()
    mentions = _find_action_mentions(semantic_text)
    if not mentions:
        mentions = _synthetic_generic_mentions(semantic_text, references)
    discourse = _classify_discourse(original, forced=forced_discourse)
    result: list[TestConstraint] = []
    seen: set[
        tuple[
            TestConstraintAction,
            TestConstraintState,
            TestConstraintScope,
            tuple[str, ...],
            ConstraintDiscourse,
        ]
    ] = set()
    for mention in mentions:
        reference = _reference_for_mention(semantic_text, mention, references)
        if reference is None:
            continue
        evidence = _state_for_mention(
            semantic_text,
            mention,
            reference,
            discourse,
            mentions,
            references,
        )
        if evidence is None:
            continue
        subtypes = reference.subtypes
        scope = (
            TestConstraintScope.SUBTYPE
            if subtypes
            else TestConstraintScope.GLOBAL
        )
        key = (mention.action, evidence.state, scope, subtypes, discourse)
        if key in seen:
            continue
        seen.add(key)
        evidence_start, evidence_end = evidence.span
        relative_start = min(mention.start, evidence_start)
        relative_end = max(mention.end, evidence_end)
        reference_follows_action = reference.start >= mention.end
        intervening = semantic_text[reference.end : mention.start]
        if reference_follows_action or not _INTERVENING_NON_TEST_INTENT_RE.search(
            intervening
        ):
            relative_start = min(relative_start, reference.start)
            relative_end = max(relative_end, reference.end)
        constraint_start = absolute_start + relative_start
        constraint_end = absolute_start + relative_end
        result.append(
            TestConstraint(
                action=mention.action,
                state=evidence.state,
                scope=scope,
                subtypes=subtypes,
                span=(constraint_start, constraint_end),
                text=original[relative_start:relative_end],
                evidence_family=evidence.family,
                discourse=discourse,
            )
        )
    return tuple(result)


def extract_test_constraints(text: str) -> tuple[TestConstraint, ...]:
    """Return ordered constraints expressed by ``text`` without side effects.

    Quoted constraint-shaped examples are returned as :attr:`META` evidence;
    callers can audit them without accidentally treating them as directives.
    Positive test requests are intentionally absent from the result.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text.strip():
        return ()

    quote_content_spans = _quote_content_spans(text)
    quote_outer_spans: list[tuple[int, int]] = []
    for match in _QUOTE_RE.finditer(text):
        quote_outer_spans.append(match.span())

    constraints: list[TestConstraint] = []
    for start, end in quote_content_spans:
        quoted = text[start:end]
        for clause_start, clause_end in _clause_spans(quoted):
            constraints.extend(
                _extract_clause(
                    quoted[clause_start:clause_end],
                    quoted[clause_start:clause_end],
                    absolute_start=start + clause_start,
                    forced_discourse=ConstraintDiscourse.META,
                    allow_bare_case=False,
                )
            )

    outside_quotes = _mask_spans(text, tuple(quote_outer_spans))
    allow_bare_case = _TEST_REFERENCE_RE.search(outside_quotes) is not None
    for start, end in _clause_spans(outside_quotes):
        constraints.extend(
            _extract_clause(
                text[start:end],
                outside_quotes[start:end],
                absolute_start=start,
                forced_discourse=None,
                allow_bare_case=allow_bare_case,
            )
        )

    constraints.sort(key=lambda item: (item.span[0], item.span[1]))
    return tuple(constraints)
