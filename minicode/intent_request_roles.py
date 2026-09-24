"""Small source-role boundaries shared by rule matching and model admission.

These helpers recognize a complete grammatical frame, not the action words
inside a quotation.  Unrecognized phrasing remains eligible for the existing
semantic fallback.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from minicode.intent_negation import ACTION_MARKER_RE


_CONTENT_NOUN_ZH = r"(?:文案|文本|文字|标签|标题|内容|消息|提示|字符串|占位符)"
_CONTENT_NOUN_EN = r"(?:label|text|caption|title|message|content|string|placeholder|prompt)"
_SOURCE_NOUN_ZH = (
    r"(?:网页|页面|按钮|控件|输入框|屏幕|界面|菜单|字段|弹窗|警告|错误|日志)"
)
_SOURCE_NOUN_EN = (
    r"(?:button|control|input|dialog|menu|field|page|warning|error|"
    r"current|existing|original)"
)
_ATTRIBUTION_PREFIX_RE = re.compile(
    r"\s*(?:(?:这个|那个|该|当前|现有|原来)?"
    rf"(?:{_SOURCE_NOUN_ZH}(?:的|上的?|里的?|中的?)?){{0,4}}{_CONTENT_NOUN_ZH}"
    r"\s*(?:是|为|写着|显示为|叫作|叫做)|"
    rf"(?:(?:the|this|that|a|an|our|its)\s+)?(?:{_SOURCE_NOUN_EN}\s+){{0,4}}"
    rf"{_CONTENT_NOUN_EN}\s+(?:is|reads|says|shows|displays))\s*[:：]?\s*",
    re.IGNORECASE,
)
_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!?;；\n]|\.(?=\s|$)")
_LITERAL_END_RE = re.compile(r"\s*(?:[,，。.!！?？;；\n]|$)")


def attributed_literal_statement_spans(
    text: str, literal_spans: Sequence[tuple[int, int]]
) -> tuple[tuple[int, int], ...]:
    """Extend quoted payloads to complete ``the label is '...'`` statements.

    The attribution must start a sentence and end at the literal.  A relative
    clause or parenthesized object in an actual request does not satisfy that
    frame.  Only the statement through its closing quote is covered, leaving
    even an unrecognized colloquial request in the next sentence visible.
    """

    spans: list[tuple[int, int]] = []
    for literal_start, literal_end in sorted(literal_spans):
        # The parser may pass its already merged protected spans back to the
        # semantic admission path. Recover the payload start only when the
        # span itself begins with this same complete attribution frame.
        contained_prefix = _ATTRIBUTION_PREFIX_RE.match(text, literal_start, literal_end)
        if contained_prefix is not None:
            literal_start = contained_prefix.end()
        if not _LITERAL_END_RE.match(text, literal_end):
            continue
        prefix_start = 0
        for boundary in _SENTENCE_BOUNDARY_RE.finditer(text, 0, literal_start):
            prefix_start = boundary.end()
        prefix = text[prefix_start:literal_start]
        if _ATTRIBUTION_PREFIX_RE.fullmatch(prefix):
            spans.append((prefix_start, literal_end))
    return tuple(spans)


def mask_attributed_literal_statements(
    text: str, protected_spans: Sequence[tuple[int, int]]
) -> str:
    """Hide only recognized attribution statements from provider requests.

    Ordinary quoted objects remain in the request.  Offsets are retained so
    an adjacent current request still receives its original production span.
    """

    characters = list(text)
    for start, end in attributed_literal_statement_spans(text, protected_spans):
        ending = _LITERAL_END_RE.match(text, end)
        if ending is not None:
            end = ending.end()
        for index in range(start, end):
            if characters[index] not in "\r\n":
                characters[index] = " "
    return "".join(characters)


_EXPLANATION_FRAME_RE = re.compile(
    r"\s*(?:(?:请|麻烦|帮我)\s*)?(?:我\s*)?(?:只|仅|只是)?\s*"
    r"(?:解释|说明|讲讲|说说|想知道)(?:一?下)?[^,，。！？!?;；\n]{0,160}"
    r"(?:含义|意思|意义|风险)(?:是什么|就好|即可|就行)?\s*[。.!！?？]?\s*|"
    r"\s*(?:please\s+)?(?:only\s+|just\s+)?"
    r"(?:explain|describe|tell\s+me(?:\s+about)?)\s+[^.!?;\n]{0,160}"
    r"(?:meaning|means|phrase|term|label|caption|text|impact|risk)"
    r"\s*[.!?]?\s*",
    re.IGNORECASE,
)
_VISIBLE_COORDINATION_RE = re.compile(
    r"[,，;；\n]|然后|再|并|另外|接着|随后|以及|还要|"
    r"\b(?:and|then|also|next|afterwards|separately)\b",
    re.IGNORECASE,
)


def is_bounded_explanation_request(masked_text: str) -> bool:
    """Recognize one explicit request for meaning, without a second command.

    This is a proposal restriction, not a classifier: the provider can still
    supply a read/analysis intent.  Visible coordination deliberately declines
    this narrow restriction because it may introduce another genuine request.
    Quoted coordination was already removed by the caller's literal mask.
    """

    return bool(
        _EXPLANATION_FRAME_RE.fullmatch(masked_text)
        and _VISIBLE_COORDINATION_RE.search(masked_text) is None
    )


# These are grammatical question heads, not a list of verbs to forbid.  In
# particular the addressee is absent from the modal-subject frame: ``Can you
# update this file?`` remains an ordinary request despite its question mark.
_ARTIFACT_SUBJECT_ZH = (
    r"(?:(?:这|那)(?:个|条|项|段)?|该|此|上述|上面的?|刚才的?)?"
    r"(?:命令|脚本|程序|函数|方法|工具|操作|参数|选项|设置|配置|补丁|改动)|"
    r"它|(?:[a-zA-Z_][\w./-]*\.(?:py|js|ts|sh))"
)
_PASSIVE_SUBJECT_ZH = (
    r"(?:(?:这|那)(?:些|个)?|该)?(?:缓存|临时|配置|日志|旧|现有|本地|"
    r"工作区的|缓存目录中的|目标目录中的){0,4}(?:文件|目录|数据|缓存)"
)
_ARTIFACT_SUBJECT_EN = (
    r"(?:it|this|that|(?:the|this|that|our)\s+"
    r"(?:command|script|program|function|method|tool|operation|option|setting|"
    r"change|patch|build|test|migration))"
)
_ADVICE_QUESTION_RE = re.compile(
    r"\s*(?:(?:(?:你|您)(?:觉得|认为)\s*)?(?:我|我们)?"
    r"(?:应该|该)?(?:怎样|如何|怎么)|"
    r"(?:这|那)(?:个|段|份|项)?[^,，。！？!?;；\n]{1,32}"
    r"(?:应该|该)(?:怎样|如何|怎么))[^。！？!?;；\n]+[?？]\s*|"
    r"\s*how\s+(?:should|would|could|can|do|does)\s+"
    rf"(?:I|we|one|{_ARTIFACT_SUBJECT_EN})\b[^!?;\n]+\?\s*",
    re.IGNORECASE,
)
_ARTIFACT_EFFECT_QUESTION_RE = re.compile(
    rf"\s*(?:为什么)?(?:{_ARTIFACT_SUBJECT_ZH})\s*(?:为什么)?"
    r"(?:是否会|是不是会|会不会|会|"
    r"是否|能否|可否|是不是)[^。！？!?;；\n]+[?？]\s*|"
    rf"\s*(?:{_PASSIVE_SUBJECT_ZH})\s*(?:是否会|会不会|会)被"
    r"[^。！？!?;；\n]+[?？]\s*|"
    r"\s*(?:will|would|does|do|could|can|should)\s+"
    rf"{_ARTIFACT_SUBJECT_EN}\b[^!?;\n]+\?\s*",
    re.IGNORECASE,
)
_DISCUSSION_ONLY_RE = re.compile(
    r"\s*(?:(?:我|我们)(?:现在|这次|本轮)?(?:想|要)?)?"
    r"(?:只|仅|只是)\s*(?:讨论|分析|评估)(?:一下)?"
    r"(?:方案|思路|影响|风险|可行性)(?:就好|就行|即可)?\s*[。.!！]?\s*|"
    r"\s*(?:please\s+)?(?:only|just)\s+(?:discuss|analy[sz]e|assess)\s+"
    r"(?:the\s+)?(?:plan|approach|impact|risks?|feasibility)\s*[.!]?\s*",
    re.IGNORECASE,
)
_NOMINAL_INFORMATION_QUESTION_RE = re.compile(
    r"\s*(?P<subject>[^,，。！？!?;；\n]{1,100}?)"
    r"(?P<question>(?:分别|各自|具体|到底)?(?:有什么(?:用|作用|用途|意义|含义)|"
    r"(?:是|做)什么|是什么意思|有何(?:意义|含义|作用)))\s*[?？]\s*",
)
_EXPLICIT_REQUEST_RESET_RE = re.compile(
    r"[,，(（]\s*(?:(?:另外|顺便|然后|接着|随后|再|同时|并且|并)\s*)?"
    r"(?=(?:请|帮我|麻烦|给我|替我))|"
    r",\s*(?:(?:and|then|also|next)\s+)*"
    r"(?=(?:please|kindly|(?:can|could|would|will)\s+you)\b)",
    re.IGNORECASE,
)
_INCIDENTAL_REQUEST_RESET_RE = re.compile(r"[,，(（]\s*(?:顺便|另外)\s*")
_ADDRESSED_ACTION_PREFIX_RE = re.compile(
    r"(?:请你|请您|帮我|帮我们|替我|替我们)\s*"
    r"(?:(?:直接|现在|立即)\s*)?|"
    r"\b(?:please|kindly|help\s+me)\s+(?:be\s+)?",
    re.IGNORECASE,
)
_READ_REQUEST_WORDS = frozenset({
    "解释", "说明", "分析", "检查", "审查", "搜索", "查找", "比较", "对比",
    "explain", "describe", "analyze", "analyse", "inspect", "review", "search",
    "find", "compare",
})


def _contains_addressed_action(sentence: str) -> bool:
    for request in _ADDRESSED_ACTION_PREFIX_RE.finditer(sentence):
        action = ACTION_MARKER_RE.match(sentence, request.end())
        if action is not None and action.group().casefold() not in _READ_REQUEST_WORDS:
            return True
    return False


def _question_request_reset(sentence: str) -> re.Match[str] | None:
    resets = list(_EXPLICIT_REQUEST_RESET_RE.finditer(sentence))
    resets.extend(
        match
        for match in _INCIDENTAL_REQUEST_RESET_RE.finditer(sentence)
        if ACTION_MARKER_RE.match(sentence, match.end()) is not None
    )
    return min(resets, key=lambda match: match.start()) if resets else None


def _is_nominal_information_question(sentence: str) -> bool:
    match = _NOMINAL_INFORMATION_QUESTION_RE.fullmatch(sentence)
    return bool(
        match is not None
        # A bare noun subject can own a wh predicate. An imperative before
        # that predicate must retain its own authority, e.g. ``修改帮助文本
        # 告诉用户这些选项有什么用？``. Let existing rules handle such input.
        and ACTION_MARKER_RE.search(match.group("subject")) is None
    )


def _request_sentences(text: str) -> tuple[tuple[str, tuple[int, int]], ...]:
    """Keep sentence-local roles without treating dots in paths as stops."""

    sentences: list[tuple[str, tuple[int, int]]] = []
    start = 0
    for boundary in _SENTENCE_BOUNDARY_RE.finditer(text):
        end = boundary.end()
        if text[start:end].strip():
            sentences.append((text[start:end], (start, end)))
        start = end
    if text[start:].strip():
        sentences.append((text[start:], (start, len(text))))
    return tuple(sentences)


def readonly_request_spans(masked_text: str) -> tuple[tuple[int, int], ...]:
    """Locate complete advisory or third-person effect questions.

    A question's subject can contain an action verb without granting action
    authority.  Its scope ends with the sentence, so a following imperative
    is still independent. Callers must mask quotations before this analysis.
    """

    return tuple(sorted((
        *readonly_question_spans(masked_text),
        *(span
        for sentence, span in _request_sentences(masked_text)
        if _DISCUSSION_ONLY_RE.fullmatch(sentence)),
    )))


def readonly_question_spans(masked_text: str) -> tuple[tuple[int, int], ...]:
    """Question frames that can supply a missing deterministic read intent.

    An explicit discussion-only constraint still leaves its semantic domain
    unresolved, so it is restricted by admission without inventing a label.
    """

    spans: list[tuple[int, int]] = []
    for sentence, (start, end) in _request_sentences(masked_text):
        if not (
            _ADVICE_QUESTION_RE.fullmatch(sentence)
            or _ARTIFACT_EFFECT_QUESTION_RE.fullmatch(sentence)
            or _is_nominal_information_question(sentence)
        ):
            continue
        reset = _question_request_reset(sentence)
        if reset is not None:
            # A comma plus a fresh polite imperative grants separate
            # authority even when the user puts one question mark at the end.
            end = start + reset.start()
        if _contains_addressed_action(sentence[:end - start]):
            # Object-fronted polite requests (``这个函数能否帮我重构`` /
            # ``Could this function please be refactored``) still address the
            # assistant. A third-person subject alone cannot make them inert.
            continue
        spans.append((start, end))
    return tuple(spans)


def question_request_resets(masked_text: str) -> tuple[tuple[int, int], ...]:
    """Expose question-to-imperative transitions to real clause matching.

    Merely shortening a role span is insufficient when one greedy verb rule
    consumed both clauses. Matching each side separately preserves the later
    command's independent action and concrete target.
    """

    resets: list[tuple[int, int]] = []
    for _start, end in readonly_question_spans(masked_text):
        reset = _question_request_reset(masked_text[end:])
        if reset is not None and reset.start() == 0:
            resets.append((end, end + reset.end()))
    return tuple(resets)


def is_discussion_action(
    masked_text: str, trigger_span: tuple[int, int]
) -> bool:
    """Whether a rule's verb belongs to a recognized read-only sentence."""

    start, end = trigger_span
    return any(
        frame_start <= start < end <= frame_end
        for frame_start, frame_end in readonly_request_spans(masked_text)
    )


def is_bounded_readonly_request(masked_text: str) -> bool:
    """Restrict model proposals only when every sentence has a read frame.

    An unrecognized sentence leaves normal semantic resolution available.
    This avoids spreading the first question's scope over an adjacent edit.
    """

    sentences = _request_sentences(masked_text)
    return bool(sentences) and all(
        any(
            not sentence[:start].strip() and not sentence[end:].strip()
            for start, end in readonly_request_spans(sentence)
        )
        or is_bounded_explanation_request(sentence)
        or is_conversational_read_request(sentence)
        for sentence, _span in sentences
    )


_CONVERSATIONAL_READ_FRAME_RE = re.compile(
    r"\s*(?:(?:先|现在|这次|本轮|目前)\s*)?"
    r"(?:(?:请|帮我|麻烦你)\s*)?(?:(?:只|仅|只是)\s*)?"
    # Reduplicated conversational heads are explicit speech acts. Ordinary
    # ``说明/解释/分析`` may instead modify a noun (``说明文字换成英文``),
    # so their existing bounded explanation/analysis frames remain in charge.
    r"(?:聊聊|谈谈|讲讲|说说)(?:一下|下)?"
    r"[^,，。！？!?;；\n]{1,160}\s*[。.!！?？]?\s*",
)
_COLLOQUIAL_INFORMATION_QUESTION_RE = re.compile(
    r"\s*(?P<subject>[^,，。！？!?;；\n]{1,100}?)"
    r"(?:是)?(?:咋|怎么|如何)[^,，。！？!?;；\n]{1,100}"
    r"(?:啊|呀|呢|吗|么|嘛)\s*[。.!！?？]?\s*",
)


def is_conversational_read_request(masked_text: str) -> bool:
    """Recognize a bounded conversational reading role, leaving its label open."""

    if _VISIBLE_COORDINATION_RE.search(masked_text) is not None:
        return False
    if _CONVERSATIONAL_READ_FRAME_RE.fullmatch(masked_text):
        return True
    question = _COLLOQUIAL_INFORMATION_QUESTION_RE.fullmatch(masked_text)
    return bool(
        question is not None
        and ACTION_MARKER_RE.search(question.group("subject")) is None
    )
