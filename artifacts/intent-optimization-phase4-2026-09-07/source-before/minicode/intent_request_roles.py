"""Small source-role boundaries shared by rule matching and model admission.

These helpers recognize a complete grammatical frame, not the action words
inside a quotation.  Unrecognized phrasing remains eligible for the existing
semantic fallback.
"""

from __future__ import annotations

import re
from collections.abc import Sequence


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
