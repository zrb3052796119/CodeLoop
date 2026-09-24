"""Shared, conservative negation recognition for intent and Skill routing.

Negation is an authority boundary: when deterministic parsing cannot fully
understand a prohibition, semantic fallback must not turn it into an
affirmative operation. Keeping the grammar here prevents the intent parser
and Skill router from drifting apart.
"""

from __future__ import annotations

import re

_ENGLISH_ACTION_PATTERN = (
    r"(?:use|using|used|load|loading|loaded|invoke|invoking|invoked|"
    r"apply|applying|applied|follow|following|followed|select|selecting|"
    r"selected|choose|choosing|chosen|include|including|included|modify|"
    r"modifying|modified|update|updating|updated|change|changes|changing|"
    r"changed|edit|edits|editing|edited|revise|revises|revising|revised|"
    r"delete|deleting|deleted|"
    r"remove|removing|removed|run|running|execute|executing|executed|touch|"
    r"touching|touched|write|writing|written|create|creating|created|fix|"
    r"fixing|fixed|repair|repairing|repaired|solve|solving|solved|resolve|"
    r"resolving|resolved|refactor|refactoring|refactored|optimize|optimizing|"
    r"optimized|improve|improving|improved|clean|cleaning|cleaned|simplify|"
    r"simplifying|simplified|restructure|restructuring|restructured|rename|"
    r"renaming|renamed|move|moving|moved|merge|merging|merged|split|"
    r"splitting|review|reviewing|reviewed|audit|auditing|audited|inspect|"
    r"inspecting|inspected|check|checking|checked|search|searching|searched|"
    r"find|finding|locate|locating|lookup|explain|explaining|explained|"
    r"describe|describing|described|analyze|analyzing|analyse|analysing|"
    r"evaluate|evaluates|evaluating|evaluated|assess|assesses|assessing|"
    r"assessed|identify|identifies|identifying|identified|profile|profiles|"
    r"profiling|profiled|benchmark|benchmarks|benchmarking|benchmarked|"
    r"compare|comparing|compared|add|adding|added|implement|implementing|"
    r"implemented|generate|generating|generated|develop|developing|developed|"
    r"test|testing|tested|verify|verifying|verified|document|documenting|"
    r"documented|comment|commenting|commented|configure|configuring|deploy|"
    r"deploying|deployed|"
    r"configured|commit|committing|committed|setup|install|installing|installed|init|initialize|initializing|"
    r"initialized)"
)
_CHINESE_ACTION_PATTERN = (
    r"(?:使用|用|调用|加载|应用|按照|选择|选用|修改|改|更新|调整|删除|删|"
    r"移除|移到|运行|执行|跑|触碰|碰|动|变|写|编写|创建|建|生成|新增|添加|实现|补齐|补充|"
    r"给(?=(?:我)?(?:写)?(?:代码|程序))|"
    r"提交|升级|升(?=一?下|版)|复现|讲清(?:楚)?|"
    r"讲(?=流程|链路|调用|大道理|空话|套话|原因|原理|过程|细节)|换掉|挪|"
    r"量(?=\s*(?:一?下|耗时|延迟|性能))|"
    r"审(?=一遍|一?下|计|核|查|\s*(?:auth|权限|安全|版本|注入))|"
    r"测(?=\s*(?:启动|初始化|性能|延迟|耗时|慢))|"
    r"重命名|改名|移动|合并|拆分|修复|修|解决|处理|调试|排查|重构|优化|整理|"
    r"简化|部署|审查|审计|审核|评审|检查|核对|复核|评价|评估|点评|审视|比较|对比|分析|"
    r"解释|说明|搜索|查找|查(?!询)|定位|找出|测试|验证|配置|设置|安装|初始化|"
    r"记录|记住|记下|保存)"
)
_CHINESE_NEGATION_ACTION_BRIDGE_PATTERN = (
    r"(?:(?:再|也|还|先|继续)\s*)?"
    r"(?:(?:(?:帮|给)\s*(?:我|我们)?|替(?:我|我们)|做|进行)\s*)?"
    r"[^。！？；,，\n]{0,24}?"
)
ACTION_MARKER_RE = re.compile(
    rf"(?<![a-z0-9_]){_ENGLISH_ACTION_PATTERN}(?![a-z0-9_])|"
    rf"{_CHINESE_ACTION_PATTERN}",
    re.IGNORECASE,
)
_FORBIDDANCE_PATTERN = (
    r"(?:forbid|forbids|forbidden|forbidding|prohibit(?:ed|s|ing)?|"
    r"disallow(?:ed|s|ing)?|ban|bans|banned|banning|"
    r"not\s+(?:allowed|permitted))"
)


FORBIDDANCE_MARKER_RE = re.compile(
    rf"\b{_FORBIDDANCE_PATTERN}\b",
    re.IGNORECASE,
)


POSITIVE_NEGATION_IDIOM_RE = re.compile(
    r"\brather\s+than\s+continuing\s+(?:that|it)\s*[,，]|"
    r"\b(?:do\s+not|don['’]?t|dont|never)\s+"
    r"(?:avoid|refrain\s+from)\s+"
    r"(?:running|executing|launching|starting|updating|changing|modifying|"
    r"editing|fixing|repairing)\b[^.!?;\n]{0,64}|"
    r"\bit\s+(?:is\s+)?(?:not|isn['’]?t)\s+(?:true|the\s+case)\s+that\s+"
    r"[^.!?;\n]{0,80}(?:should|must|can|may)\s+not\s+"
    r"(?:run|execute|launch|start|update|change|modify|edit)\b|"
    r"\bit\s+is\s+not\s+the\s+case\s+that\s+[^.!?;\n]{0,80}"
    r"(?:must|should|has\s+to)\s+remain\s+unchanged\b|"
    r"\bit\s+is\s+false\s+that\s+[^.!?;\n]{0,80}"
    r"(?:off[ -]?limits|forbidden|prohibited)\b|"
    r"\b(?:we\s+)?cannot\s+leave\s+(?:pytest|tests?|test\s+suite)\s+"
    r"(?:unrun|unexecuted|not\s+run)\b|"
    r"\b(?:you\s+are\s+|we\s+are\s+|it\s+is\s+)?not\s+"
    r"(?:forbidden|prohibited)\s+from\s+"
    r"(?:running|executing|launching|starting|updating|changing|modifying|"
    r"editing|fixing|repairing)\b[^.!?;\n]{0,64}|"
    r"\bwithout\s+(?:hesitation|delay)\b|"
    r"\b(?:do\s+not|don['’]?t|dont)\s+(?:skip|avoid)\s+"
    r"(?:(?:any|all|the)\s+)?(?:pytest|tests?|test\s+suite)\b|"
    r"\b(?:do\s+not|don['’]t|do\s+n['’]t|dont|never|must\s+not|"
    r"should\s+not|mustn['’]t|shouldn['’]t|cannot|can['’]t|"
    r"be\s+sure\s+not\s+to)\s+"
    r"(?:ever\s+)?(?:hesitate|forget|fail|neglect)\s*(?:to\b|:\s*)|"
    r"\b(?:do\s+not|don['’]t|dont|never)\s+"
    r"(?:stop(?:ping)?|cease|discontinue)\s+|"
    r"\bwithout\s+fail(?:ing)?\b|"
    r"(?:不要|别)\s*(?:跳过|略过|避开)\s*"
    r"(?:所有|全部|任何)?\s*(?:pytest|测试|单测|测例|用例)|"
    r"(?:不要|别)\s*(?:再\s*)?(?:避免|拒绝|不)\s*"
    r"(?:修复|修|修改|更新|改|跑|运行|执行|启动)"
    r"[^。！？；\n]{0,64}|"
    r"(?:不是|并非)(?:说|在说|让|让你|叫|叫你)?\s*"
    r"(?:不能|不可|不准|不许|禁止|别|不要)\s*"
    r"(?:修复|修|修改|更新|改|跑|运行|执行|启动)"
    r"[^。！？；\n]{0,64}|"
    r"(?:测试|pytest|单测|测例|用例)[^。！？；\n]{0,24}"
    r"(?:并非|不是)\s*(?:禁止|不许|不准)\s*(?:执行|运行|跑)?|"
    r"(?:不能|不可|不应)\s*(?:跳过|略过)\s*"
    r"(?:所有|全部|任何)?\s*(?:pytest|测试|单测|测例|用例)|"
    r"(?:不要|别|切勿|勿|不能|不可|不应)\s*(?:忘记|忘了|犹豫)|"
    r"(?:可\s*)?别\s*不\s*"
    r"(?:改|修|做|写|跑|运行|执行|更新|重构|优化|测试|审查|查|排查|诊断)",
    re.IGNORECASE,
)


_NEGATION_SCOPE_RESET_RE = re.compile(
    r"[!?;\n。！？；]|\.(?![a-z0-9_/\\-])|"
    r"\b(?:but|instead|rather)\b|"
    r"[,;—]\s*(?:actually|instead)\b|"
    r"(?:但是|但|而是|[,，；—](?:哦不|不对)[,，]?(?:还是)?)",
    re.IGNORECASE,
)


_DISCOURSE_DECLINE_RE = re.compile(
    r"^\s*(?:(?:先|暂时)\s*)?(?:不用了|不用啦|算了)"
    r"(?:\s+|[,，。.!；;]+\s*)",
    re.IGNORECASE,
)


NEGATION_MARKER_RE = re.compile(
    r"\b(?:"
    r"do\s+not|don['’]t|do\s+n['’]t|dont|never|"
    r"no\s+longer|instead\s+of|rather\s+than|"
    r"must\s+not|mustn['’]?t|"
    r"should\s+not|shouldn['’]?t|"
    r"can\s*not|cannot|can['’]?t|"
    r"(?:will|would|could|shall|should|must|can|may|might)\s+not|"
    r"ought\s+not\s+to|"
    r"under\s+no\s+circumstances|in\s+no\s+circumstances|"
    r"not\s+under\s+any\s+circumstances|by\s+no\s+means|"
    r"on\s+no\s+account|in\s+no\s+case|at\s+no\s+time|"
    r"won['’]t|wouldn['’]t|couldn['’]t|shan['’]t|"
    r"(?:isn|aren)['’]t\s+(?:allowed|permitted)\s+to|"
    r"not\s+(?:(?:allowed|permitted)\s+)?to|"
    r"not\s+asking\s+(?:you\s+)?to|"
    rf"not\s+(?={_ENGLISH_ACTION_PATTERN}(?![a-z0-9_]))|"
    rf"no\s+(?={_ENGLISH_ACTION_PATTERN}(?![a-z0-9_]))|"
    r"no\s+(?=(?:deploy(?:ment)?|edits?|code\s+changes?)\b)|"
    r"no\s+need\s+to|without|avoid|skip|exclude|stop(?:ped|ping)?|"
    r"cease|discontinue(?:d|ing)?|"
    r"refrain\s+from|"
    r"except(?:\s+to)?|anything\s+but|"
    rf"{_FORBIDDANCE_PATTERN}"
    r")\b|"
    r"\bleave\b(?:[^.!?;\n。！？；]|\.(?=[a-z0-9_/\\-])){0,80}"
    r"\b(?:alone|unchanged)\b|"
    r"\bkeep\b(?:[^.!?;\n。！？；]|\.(?=[a-z0-9_/\\-])){0,80}"
    r"\bunchanged\b|"
    r"\b(?:no|zero)\s+(?:code\s+)?(?:changes?|edits?|modifications?)\b|"
    r"(?:不要|不用|不需要|不必|无需|无须|甭|禁止|严禁|不准|不许|不允许|(?<!会)不会|"
    r"不再|不做|拒绝|谢绝|杜绝|"
    rf"(?<![分区类性个特识差辨告])别(?!的)(?=\s*"
    rf"{_CHINESE_NEGATION_ACTION_BRIDGE_PATTERN}{_CHINESE_ACTION_PATTERN})|"
    r"取消|停止|请勿|不得|切勿|"
    r"(?:^|(?<=[,，。.!?；;\n]))\s*"
    r"(?:(?:请(?:你)?|先|现在|暂时)\s*)?(?:撤回|收回)|"
    rf"没(?:有)?\s*(?:让|叫|要求)(?:你|我)?(?=\s*{_CHINESE_ACTION_PATTERN})|"
    rf"(?:不是|并非)\s*(?:让|叫|要|要求)(?:你|我)?(?=\s*{_CHINESE_ACTION_PATTERN})|"
    rf"(?:不是|并非)\s*(?:让|叫|要|要求)(?:你|我)?\s*"
    rf"(?={_ENGLISH_ACTION_PATTERN}(?![a-z0-9_]))|"
    r"(?:不是|并非)(?:在)?(?:做|进行|运行|执行)|"
    r"零(?=(?:代码)?(?:改动|修改|变更))|"
    r"不可|不应|不能|勿|莫|避免|跳过|避开|排除|并非要|不是要|"
    rf"不\s*(?:(?:实际|真的?|直接)\s*)?(?={_CHINESE_ACTION_PATTERN})|"
    rf"(?:不要|别|不准|不许|请勿|勿|不)\s*"
    rf"(?={_ENGLISH_ACTION_PATTERN}(?![a-z0-9_]))|"
    r"(?:保持|维持)[^。！？；\n]{0,40}(?:不变|原样))",
    re.IGNORECASE,
)


_ALTERNATIVE_NEGATION_RE = re.compile(
    r"\b(?:instead\s+of|rather\s+than)\b",
    re.IGNORECASE,
)


def effective_negation_matches(text: str) -> tuple[re.Match[str], ...]:
    """Return negators that are not part of a positive idiom.

    Phrases such as ``don't forget to review`` contain a surface negator but
    affirm the requested action. Only the marker covered by that idiom is
    ignored; another prohibition later in the same clause still applies.
    """

    idiom_spans = tuple(
        match.span() for match in POSITIVE_NEGATION_IDIOM_RE.finditer(text)
    )
    matches: list[re.Match[str]] = []
    for match in NEGATION_MARKER_RE.finditer(text):
        if any(start <= match.start() < end for start, end in idiom_spans):
            continue
        matches.append(match)
    return tuple(matches)


def is_clause_negated(text: str) -> bool:
    """Return whether a clause contains an effective prohibition."""

    return bool(effective_negation_matches(text))


def first_action_span(
    text: str, within: tuple[int, int]
) -> tuple[int, int] | None:
    """Return the first explicit action marker inside a parser match span."""

    start, end = within
    if not 0 <= start <= end <= len(text):
        raise ValueError("within must be within text")
    match = ACTION_MARKER_RE.search(text, start, end)
    return match.span() if match is not None else None


def scope_start_for_span(text: str, span: tuple[int, int]) -> int:
    """Return the local start of the affirmative scope containing ``span``."""

    start, end = span
    if not 0 <= start <= end <= len(text):
        raise ValueError("span must be within text")
    preceding = tuple(
        match for match in effective_negation_matches(text) if match.start() < start
    )
    if not preceding:
        return 0
    resets = tuple(
        _NEGATION_SCOPE_RESET_RE.finditer(text, preceding[-1].end(), start)
    )
    return resets[-1].end() if resets else 0


def _completed_nonuse_status_ends_before(
    text: str, negator: re.Match[str], action_start: int
) -> bool:
    """A finished non-use status is not a prohibition of a later command.

    Keep action cancellations (``deleting it is no longer needed``) and
    present prohibitions (``no longer use it``) effective. Only a declarative
    subject, a completed state, and an explicit comma boundary close this scope.
    """

    prefix = re.split(r"[,，。！？!?;；\n]", text[: negator.start()])[-1]
    if not prefix.strip() or ACTION_MARKER_RE.search(prefix):
        return False
    suffix = text[negator.end() : action_start]
    if re.fullmatch(r"不用|不需要", negator.group(0)):
        return re.match(r"\s*(?:了|啦)\s*[,，]", suffix) is not None
    if re.fullmatch(r"no\s+longer", negator.group(0), re.IGNORECASE):
        return bool(
            re.search(r"\b(?:is|are|was|were)\s*$", prefix, re.IGNORECASE)
            and re.match(
                r"\s+(?:used|needed|required|in\s+use)\s*[,，]",
                suffix,
                re.IGNORECASE,
            )
        )
    return False


def is_span_negated(text: str, span: tuple[int, int]) -> bool:
    """Return whether an effective prohibition governs one operation span.

    This deliberately answers a narrower question than :func:`is_clause_negated`:
    a contrast may reject an earlier proposal and affirm a later operation in the
    same coarse parser clause.  Passive forbiddance (``editing is forbidden``)
    remains covered so narrowing the scope does not create write authority.
    """

    start, end = span
    if not 0 <= start <= end <= len(text):
        raise ValueError("span must be within text")

    matches = effective_negation_matches(text)
    discourse_declines = tuple(_DISCOURSE_DECLINE_RE.finditer(text))
    preceding = tuple(
        match
        for match in matches
        if match.start() < start
        and not any(
            decline.start() <= match.start() < decline.end() <= start
            for decline in discourse_declines
        )
    )
    if preceding:
        latest = preceding[-1]
        governed_prefix = text[latest.end() : start]
        if _ALTERNATIVE_NEGATION_RE.fullmatch(latest.group(0)):
            # ``rather than modify A, explain B`` rejects only the first
            # action after the marker. A later action in the same sentence
            # starts the positive alternative even without the word ``but``.
            earlier_action = ACTION_MARKER_RE.search(
                text,
                latest.end(),
                start,
            )
            if earlier_action is not None:
                return False
        if (
            not _NEGATION_SCOPE_RESET_RE.search(governed_prefix)
            and not _completed_nonuse_status_ends_before(text, latest, start)
        ):
            return True

    # In passive forms the forbiddance follows the operation. Bound it to the
    # current sentence so a prohibition in a later sentence cannot leak back.
    sentence_end_match = re.search(
        r"[!?;\n。！？；]|\.(?![a-z0-9_/\\-])",
        text[end:],
        re.IGNORECASE,
    )
    sentence_end = (
        end + sentence_end_match.start()
        if sentence_end_match is not None
        else len(text)
    )
    return bool(FORBIDDANCE_MARKER_RE.search(text[end:sentence_end]))
