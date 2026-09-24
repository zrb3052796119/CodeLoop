"""Intent Parser - Structured user intent parsing layer.

Inspired by: raw material -> clean expression -> task path -> target skill
Transforms user input into stable intent objects before routing.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

from minicode.intent_constraints import (
    ConstraintDiscourse,
    TestConstraintAction,
    TestConstraintState,
    extract_test_constraints,
)
from minicode.intent_entity_targets import (
    FILE_TOKEN_RE,
    extract_file_tokens,
    has_file_reference,
    select_referenced_files,
)
from minicode.intent_negation import (
    ACTION_MARKER_RE,
    POSITIVE_NEGATION_IDIOM_RE,
    effective_negation_matches,
    first_action_span,
    is_clause_negated,
    is_span_negated,
    scope_start_for_span,
)
from minicode.intent_request_roles import (
    attributed_literal_statement_spans,
    is_bounded_explanation_request,
    is_bounded_readonly_request,
    is_discussion_action,
    mask_attributed_literal_statements,
    readonly_question_spans,
    question_request_resets,
)
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


class IntentPolarity(str, Enum):
    """Whether an operation is requested or explicitly ruled out."""

    AFFIRMED = "affirmed"
    NEGATED = "negated"


class EvidenceSource(str, Enum):
    """Stable provenance categories for an intent decision."""

    EXPLICIT = "explicit"
    RULE = "rule"
    CONTEXT = "context"
    SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class IntentEvidence:
    """One bounded, user-displayable reason for an intent decision."""

    code: str
    source: EvidenceSource
    reason: str
    strength: float
    span: tuple[int, int] | None = None
    turn_offset: int = 0


@dataclass(frozen=True, slots=True)
class IntentOperation:
    """One ordered operation found in a possibly compound request."""

    intent_type: IntentType
    action_type: ActionType
    polarity: IntentPolarity
    confidence: float
    clause: str
    span: tuple[int, int] | None = None
    entities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    evidence_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntentDiagnostics:
    """Operational diagnostics without prompt text or hidden reasoning."""

    semantic_status: str = "disabled"
    context_turns_used: int = 0
    warnings: tuple[str, ...] = ()


_VIEW_SWITCH_MODIFIER_ZH_PATTERN = (
    r"(?:(?:先|继续|只|仅|再|仔细|认真|暂时|重新|only)\s*){0,4}"
)
_VIEW_SWITCH_VERB_PATTERN = (
    r"(?:查看|检查|审查|阅读|分析|看|查|审|读|review\b|inspect\b|"
    r"look\s+at\b|check\b|analy[sz]e\b|read\b)"
)
_VIEW_SWITCH_AFTER_CHANGE_PATTERN = (
    rf"(?:\s*(?:为|成|去)\s*{_VIEW_SWITCH_MODIFIER_ZH_PATTERN}"
    rf"{_VIEW_SWITCH_VERB_PATTERN}|\s*{_VIEW_SWITCH_VERB_PATTERN})(?:一?下)?"
)
_ENGLISH_VIEW_SWITCH_PREFIX_PATTERN = (
    r"(?:switch(?:\s+over)?\s+to|"
    r"change\s+(?:the\s+)?focus(?:\s+over)?\s+to|"
    r"shift\s+(?:(?:your|the)\s+)?focus(?:\s+over)?\s+to|"
    r"(?<!the\s)(?<!this\s)(?<!that\s)change\s+to)"
)


_CODE_PATTERNS = [
    (
        # An explicit existing source file makes an added behavior an update,
        # including object-fronted requests followed by a separate document.
        r"(?:给|为|在)\s*[\w/\\._-]+\."
        r"(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h)\b\s*(?:中|里)?\s*"
        r"(?P<action_extend_source>增加|添加|新增|补充)"
        r"[^,，。！？;；\n]{0,64}(?:逻辑|功能|支持|处理)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?P<action_release_unchanged>it\s+is\s+not\s+the\s+case\s+that)\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"json|yaml|yml|toml)\s+(?:must|should|has\s+to)\s+"
        r"remain\s+unchanged\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:把|将)\s*[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"json|yaml|yml|toml)\s*(?:里|中的?|里的?)\s*"
        r"[^,，。！？!?;；\n]{0,32}(?:逻辑|实现|代码)\s*"
        r"(?:直接\s*)?(?P<action_named_logic_update>改好|修好|更新|修改)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?P<action_apply_change>apply)\s+(?:the\s+|this\s+|that\s+)?"
        r"(?:requested\s+|proposed\s+)?change\s+to\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"json|yaml|yml|toml)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?P<action_file_patch>patch|rewrite)\b\s+(?:the\s+)?"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"json|yaml|yml|toml)\b|"
        r"(?P<action_file_rewrite_zh>重写)(?:一?下)?\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"json|yaml|yml|toml)\b|"
        r"(?P<action_file_patch_zh>补(?!跑|充说明))(?:一?下)?\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"json|yaml|yml|toml)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:不要|别|甭|不用|不必|无需|无须)\s*"
        r"(?P<action_give>给)(?:我)?(?:写)?(?:代码|程序)",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        # Compact Chinese TDD sequencing often omits both the object and
        # punctuation: ``先测试再实现`` / ``实现随后``.  The right-hand
        # anchors keep a bare discussion of "实现" from becoming write
        # authority.
        r"(?:再|然后)(?P<action_tdd>实现)(?=\s*$)|"
        r"(?P<action_postfix>实现)\s*随后(?=\s*(?:[,，]|$))",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?P<action_tdd_written>写实现)"
        r"(?=\s*(?:$|[,，。！？!?;；]))|"
        r"(?P<action_auth_impl>实现).{0,24}"
        r"(?:登录|鉴权|认证|授权|权限|校验|验证)",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?:refs?|references?|引用|imports?).{0,24}"
        r"(?P<action>改|更新|同步|跟着|一起改)|"
        r"(?P<action_first>更新|同步|改).{0,24}"
        r"(?:refs?|references?|引用|imports?)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:ruff|flake8|mypy|lint(?:er)?).{0,24}"
        r"(?P<action>清一?下|修|改|fix|clean)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?P<action>落个|落一个|做个|建个)\s*commit\b|"
        r"(?:不要|别|甭|do\s+not|don['’]?t)\s*(?P<action_commit>commit)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    # A request to implement something *with tests* contains two deliverables:
    # production code and test coverage.  Keep this match on the implementation
    # verb so the test marker below can become a separate operation segment.
    (
        (
            r"\b(?:implement|build|develop)\b(?=[^.!?;\n]{0,64}"
            r"\b(?:with|including)\b[^.!?;\n]{0,24}"
            r"\b(?:test\s+coverage|tests?)\b)"
        ),
        IntentType.CODE,
        ActionType.CREATE,
    ),
    # A bounded gap (not just \s+) so "write a NEW function"/"the code" still
    # match — a determiner or short adjective between the trigger verb and
    # its target noun is the normal way English phrases these requests.
    # "codes?" must live here, ahead of _DOCUMENT_PATTERNS, or the most common
    # coding request ("write the code") is claimed by the document intent.
    (
        r"\b(?:write|create|implement|add|generate)\b.{0,24}\b(?:functions?|classes?|methods?|modules?|components?|pages?|apis?|codes?|features?|endpoints?|handlers?|cli|commands?|scripts?|authentication|auth|logging|validation)\b",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"\b(?:modify|update|change|fix)\b.{0,20}\b(?:codes?|files?|functions?|classes?|methods?)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:implement|complete|develop)\b.{0,20}\b(?:features?|tasks?|requirements?)\b",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        (
            r"\b(?:run|execute)\b.{0,24}\b"
            r"(?:ruff|flake8|mypy|lint(?:er)?|static\s+analysis)\b|"
            r"\b(?:clean\s+up|fix)\b.{0,48}\b"
            r"(?:ruff|flake8|mypy|lint(?:er)?|static\s+analysis|warnings?)\b"
        ),
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:update|rewrite|fix)\b.{0,40}\b(?:imports?|references?)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        # Do not let an outer, metalinguistic ``commit`` reach across a
        # negation and borrow the object of a later denied action, as in
        # ``commit 不是叫你 commit changes``.  Naming the action group also
        # makes the second ``commit`` the polarity anchor when the regex
        # retries after the boundary.
        r"\b(?P<action_commit_en>commit)\b"
        r"(?:(?!\b(?:not|without)\b|\bisn['’]?t\s+asking\b|不是|并非)"
        r"[^.!?;。！？；\n]){0,40}"
        r"\b(?:changes?|edits?|work|files?)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:不要|不用|不必|无需|无须|别|甭|不)\s*"
        r"(?P<action>提交)(?=$|[\s,，。！？;；]|只|先|再|然后)|"
        r"(?P<action_positive>提交).{0,24}(?:代码|改动|变更|文件)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:design|build|create|implement)\b.{0,80}\b(?:frontends?|"
        r"landing\s+pages?|components?|layouts?|multi[- ]agents?|"
        r"sub[- ]agents?|orchestrations?|delegations?)\b",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?:设计|构建|实现).{0,80}"
        r"(?:前端|页面|组件|布局|多智能体|多代理|子代理|任务委派|编排)",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?:写|编写|创建|生成|新增|添加|实现).{0,50}"
        r"(?:代码|程序|函数|类|方法|模块|组件|页面|接口|功能|命令行|脚本)",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        rf"(?:修改|改(?!{_VIEW_SWITCH_AFTER_CHANGE_PATTERN})"
        r"(?:一?下)?(?!动)|更新|调整|补齐|优化).{0,50}"
        r"(?:代码|逻辑|函数|类|方法|模块|组件|页面|接口|功能|配置|注释|"
        r"\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:重做|改造|重新布局).{0,80}"
        r"(?:前端|页面|组件|布局|移动端|响应式|ui)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:修复|调整|修改).{0,80}"
        r"(?:前端|页面|组件|布局|移动端|响应式|ui)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:让|使).{0,80}(?:页面|设置页|登录页|组件|手机|移动端).{0,80}"
        r"(?:不再)?(?:溢出|错位|遮挡|变形)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:重新设计|安排(?:成)?|分给|拆分|拆开|实施).{0,100}"
        r"(?:多智能体|多代理|工作单元|子代理|并行|分工|工作流|模块)",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?:把|将).{0,100}(?:模块|任务|工作).{0,60}"
        r"(?:并行分工|分给.{0,20}(?:工作单元|子代理)|拆成.{0,20}(?:工作单元|子代理))",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?:页面|设置页|按钮|组件|布局|手机|移动端).{0,80}"
        r"(?:挤出|溢出|错位|遮挡|横着滚).{0,40}"
        r"(?:弄|整|调|修)(?:到)?(?:正常|好|顺|利索)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:behavio(?:u)?r|行为|外部行为).{0,24}"
        r"(?:must\s+not\s+change|unchanged|别变|不要变|保持不变)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"\b[A-Z][A-Za-z0-9_]*(?:Page|View|Component|Screen)\b"
        r".{0,40}(?:修|改|fix|repair)|"
        r"(?:修|改|fix|repair).{0,40}"
        r"\b[A-Z][A-Za-z0-9_]*(?:Page|View|Component|Screen)\b",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:把|将).{0,80}(?:路由|评测|模块|功能|组件).{0,80}"
        r"(?:分开|拆开).{0,24}(?:并行)?(?:整|做|实现)",
        IntentType.CODE,
        ActionType.CREATE,
    ),
]

_DEBUG_PATTERNS = [
    (
        r"(?P<action_handle>处理)[^,，。！？;；\n]{0,32}"
        r"(?:问题|缺陷|故障|bug)",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        r"(?P<action_short_fix>改|修)[^,，。！？;；\n]{0,32}"
        r"(?:bug|问题|缺陷|故障)|"
        r"(?:bug|问题|缺陷|故障|失效|异常|失败)"
        r"[^,，。！？;；\n]{0,16}"
        r"(?:(?:必须|得|要|需(?:要)?)\s*)?"
        r"(?P<action_short_fix_tail>修|修复|解决|处理|修好|修掉|改好|改掉)",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        r"\b(?P<action>profile)\b.{0,64}\b"
        r"(?:slow\s+)?(?:(?:database|sql)\s+)?quer(?:y|ies)\b",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>测)(?:一?下)?(?:启动|初始化|命令|接口|请求)"
        r".{0,32}(?:慢|耗时|延迟|瓶颈)"
        r".{0,24}(?:哪里|在哪|原因|为什么|为何)?",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?:debug|troubleshoot|investigate)\b.{0,24}\b(?:errors?|bugs?|issues?|problems?|exceptions?|failures?)\b",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?P<action_fix>fix|solve|resolve|repair|fixing|solving|"
        r"resolving|repairing)\b.{0,24}"
        r"\b(?:errors?|bugs?|issues?|problems?|exceptions?|failures?|"
        r"regressions?)\b",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        (
            r"\b(?:tests?|commands?|jobs?|processes?)\b.{0,48}"
            r"\b(?:fails?|hangs?|breaks?)\b.{0,32}"
            r"\b(?:intermittently|sometimes|randomly|race|deadlock)\b|"
            r"\b(?:intermittent|flaky)\b.{0,48}"
            r"\b(?:failures?|races?|deadlocks?|tests?)\b"
        ),
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?:find|locate|identify|diagnose)\b.{0,48}\b(?:deadlocks?|races?)\b",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        (
            r"\b(?:commands?|requests?|jobs?|processes?|builds?|startup)\b"
            r".{0,64}\b(?:takes?|spends?)\b.{0,32}"
            r"\b(?:seconds?|minutes?|ms|milliseconds?)\b.{0,48}"
            r"\b(?:why|cause|reason|bottleneck)\b|"
            r"\bprofile\b.{0,64}\b(?:startup|hot\s*path|command|request)\b"
            r".{0,64}\b(?:bottleneck|slow|latency)\b"
        ),
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:what|why)\s+(?:is|does)\s+(?:wrong|error|fail|broken)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:why|how).{0,50}(?:login|auth|build|test|code|function|api).{0,30}(?:fail|broken|error|wrong)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:调试|排查).{0,80}(?:错误|报错|失败|异常|问题|bug)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:修复|解决).{0,80}(?:错误|报错|失败|异常|问题|缺陷|bug)",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        r"(?:(?<!刚才)定位|查明|找出|弄清|搞清|调查).{0,100}"
        r"(?:根因|失败|异常|报错|慢|耗时|cpu|热点|锁等待|"
        r"死锁|竞争|卡死|"
        r"瓶颈|性能|全表扫描|隔离)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:为什么|为何).{0,80}(?:失败|报错|异常|变慢|很慢|扫全表|未召回|"
        r"没有召回|检索不到)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:列出|罗列|查找|定位|找出)"
        r"(?![^。！？；\n]{0,100}(?:修复|解决|修改|更新|删除|移除|重命名|"
        r"\b(?:fix|repair|solve|resolve|modify|update|delete|remove|rename)\b))"
        r".{0,80}"
        r"(?:错误|报错|异常|失败|缺陷|\bbugs?\b|\berrors?\b|\bfailures?\b)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:\b(?:tracebacks?|failures?|errors?)\b.{0,80}\bpytest\b|"
        r"\bpytest\b.{0,80}\b(?:tracebacks?|failures?|fails?|errors?)\b)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:\b(?:pytest|pytes|tests?|assertions?|apis?|interfaces?)\b|"
        r"测试|断言|接口).{0,80}"
        r"(?:红了|挂了|抽风|炸了|磨磨唧唧|卡住|borked|kaboom)"
        r".{0,80}(?:为啥|咋回事|啥原因|哪抽风|烧哪|原因|怎么回事|"
        r"clue|why|figure\s+out|what\s+happened|inspect|diagnose|看|瞅|扒)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:为啥|咋回事|啥原因|怎么回事|why).{0,80}"
        r"(?:挂了|红了|抽风|炸了|失败|报错|fails?|failed|broken)|"
        r"(?:看|检查|分析|inspect).{0,40}(?:tracebacks?|堆栈|报错)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?:diagnose|inspect|investigate)\b.{0,64}"
        r"\b(?:deadlocks?|worker\s+hangs?|tracebacks?)\b|"
        r"(?:诊断|分析|排查|看看).{0,64}(?:死锁|卡死|堆栈)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:修复|修好|修完|修)(?:完|好)?"
        r"(?=$|[\s,，。！？;；]|(?:后|以后|再))",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:fix|repair)\b.{0,20}\b(?:it|this|that)\b",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
]

# These reports are narrower than the generic REVIEW vocabulary below.  Keep
# them in an early lane so colloquial ``look/check/inspect`` wording does not
# turn a failing test or a measured latency investigation into a code review.
_PRECISE_DEBUG_PATTERNS = [
    (
        # The requested output is a cause of observed software behavior.
        # These diagnostic goals outrank a preceding generic reading step.
        r"(?P<action_causal_diagnosis>分析|诊断|排查)"
        r"[^,，。！？;；\n]{0,48}"
        r"(?:错误|故障|失败|异常|报错|缓存|命中率|成功率|延迟|耗时|吞吐量)"
        r"[^,，。！？;；\n]{0,48}(?:原因|根因)|"
        r"\b(?P<action_diagnose_cause>diagnose|investigate)\s+"
        r"(?:the\s+)?(?:root\s+)?cause\b",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:只|仅)\s*(?P<action_checkout_probe>看|查|分析)(?:一?下)?"
        r"[^,，。！？;；\n]{0,32}(?:结账|支付|订单)"
        r"[^,，。！？;；\n]{0,24}(?:服务|接口|链路|查询)"
        r"[^,，。！？;；\n]{0,24}(?:性能|瓶颈|慢查询|延迟|耗时)|"
        r"(?:结账|支付|订单)[^,，。！？;；\n]{0,24}"
        r"(?:服务|接口|链路|查询)[^,，。！？;；\n]{0,24}"
        r"(?:性能|瓶颈|慢查询|延迟|耗时)"
        r"[^,，。！？;；\n]{0,16}"
        r"(?P<action_checkout_probe_tail>(?:只|仅)\s*(?:看|查|分析))",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:只|仅)?(?P<action_log_read>读|看|分析)(?:一?下)?\s*"
        r"(?:失败|报错)?(?:日志|logs?|tracebacks?|堆栈)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?P<action>profile)\b.{0,80}"
        r"(?:\b(?:api|app|service|server)\b|"
        r"[\w/\\._-]+\.(?:py|js|ts|go|rs|java))"
        r".{0,48}(?:启动|startup).{0,24}"
        r"(?:耗时|慢|延迟|latency|slow|bottleneck)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action_log>读|看|分析)(?:一?下)?"
        r"[^,，。！？;；\n]{0,32}(?:pytest\s*)?"
        r"(?:失败|报错)(?:日志)?"
        r"[^,，。！？;；\n]{0,24}(?:原因|结论|线索)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>查|排查|诊断)(?:一?下|下)?[^,，。！？;；\n]{0,24}"
        r"(?:deadlock|死锁)|"
        r"(?:deadlock|死锁)[^,，。！？;；\n]{0,24}"
        r"(?P<action_tail>查|排查|诊断)(?:一?下|下)?",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:卡顿|很慢|变慢|延迟|耗时|性能).{0,32}"
        r"(?P<action>量)(?:一?下|耗时|延迟|性能)|"
        r"(?P<action_first>量)(?:一?下).{0,32}"
        r"(?:卡顿|很慢|变慢|延迟|耗时|性能)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:锁|线程|并发|deadlock|worker\s+pool).{0,48}"
        r"(?:偶发|随机|间歇)?.{0,24}(?:挂住|卡死|死锁|竞争)"
        r".{0,24}(?P<action>查(?:一?下|下)?|排查|诊断)|"
        r"(?P<action_first>查(?:一?下|下)?|排查|诊断).{0,32}"
        r"(?:锁|并发|deadlock).{0,32}(?:挂住|卡死|死锁|竞争)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:pytest|pytes|测试|断言).{0,48}"
        r"\broot\s+cause\b|"
        r"(?P<action>查|看|排查).{0,24}(?:pytest|pytes)"
        r".{0,24}(?:失败|报错|红了|挂了)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>查|看|找)(?:一?下)?(?:慢|耗时|延迟|瓶颈)"
        r".{0,24}(?:在哪|哪里|哪点|原因)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:\b(?:worker\s+pool|threads?|locks?|concurrency)\b|"
        r"(?:线程|锁|并发)).{0,96}(?:\b(?:deadlock|race|contention)\b|死锁|竞争)"
        r".{0,96}(?:\b(?:analy[sz]e|inspect|diagnose|investigate)\b|分析|排查)|"
        r"(?:\b(?:analy[sz]e|inspect|diagnose|investigate)\b|分析|排查)"
        r".{0,96}(?:\b(?:threads?|locks?|concurrency|deadlock|race|contention)\b|"
        r"线程|锁|并发|死锁|竞争)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:\b(?:pytest|pytes|tests?|assertions?)\b|测试|断言)"
        r".{0,80}(?:红了|挂了|抽风|炸了|fails?|failed|borked)"
        r".{0,80}(?:为啥|咋回事|啥原因|哪抽风|原因|根因|why|clue|"
        r"figure\s+out|看|看看|瞅瞅|搞明白|弄明白)|"
        r"(?:\b(?:pytest|pytes|tests?)\b|测试|断言).{0,64}"
        r"(?:为啥|为什么|why).{0,24}(?:挂|失败|fail)"
        r".{0,32}(?:搞明白|弄明白|figure\s+out)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:\b(?:inspect|profile|diagnose|investigate)\b.{0,80}"
        r"\b(?:api|request|endpoint|service)\b.{0,80}"
        r"\b(?:latency|p\d{2,3}|slow|bottleneck)\b|"
        r"\b(?:api|request|endpoint|service)\b.{0,80}"
        r"\b(?:latency|p\d{2,3}|slow|bottleneck)\b.{0,80}"
        r"\b(?:inspect|profile|diagnose|investigate)\b)|"
        r"(?:接口|请求|服务).{0,80}(?:延迟|耗时|慢|磨磨唧唧)"
        r".{0,80}(?:烧哪|耗哪|去哪|原因|瓶颈|扒|查(?!询)|看)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:不用|不要|别|无需|无须|不必|do\s+not|don['’]?t|dont|"
        r"no\s+need\s+to)?\s*"
        r"(?:查|找|看|瞅|定位|inspect|find|locate|check).{0,24}"
        r"(?:根因|原因|为啥|why|cause|reason)",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
]

_REFACTOR_PATTERNS = [
    (
        r"(?:慢查询|查询性能(?:问题|瓶颈)?|查询延迟)"
        r"[^,，。！？!?;；\n]{0,32}"
        r"(?P<action_postfix_query_optimize>(?:继续|再|重新)?\s*(?:优化|调优))",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?:\b(?:do\s+not|don['’]?t|dont|never)\s+"
        r"(?P<action>refactor)\b|"
        r"(?:不要|别|甭|不用|不必|无需|无须)\s*"
        r"(?P<action_zh>重构|优化))",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:refactor|refacor|optimize|improve|clean|simplify|restructure)\b"
        r".{0,24}\b(?:codes?|structures?|logic|designs?|modules?|functions?|"
        r"classes?|implementations?|parsers?|routers?)\b|"
        r"\b(?:safe\s+)?(?:parsers?|routers?|modules?|codebases?)\s+refactor\b",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?:重构|整理|简化|优化).{0,50}(?:代码|结构|逻辑|设计|模块|函数|类|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h))"
        r"(?![^。！？;\n]{0,24}(?:(?:已经|已)\s*"
        r"(?:顺利|成功|彻底|全部|完全)?\s*(?:完成|结束|做完)|"
        r"(?:完成|结束|做完)\s*(?:了|啦|完毕)))",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?:瓶颈|热点|性能问题|性能毛病)"
        r"[^,，。！？!?;；\n]{0,24}"
        r"(?P<action_reverse>优化|调优)",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?:减少|避免|消除).{0,80}(?:全表扫描|重复查询|锁竞争|性能开销)|"
        r"(?:优化|调优).{0,80}(?:瓶颈|查询|索引|访问路径|运行时|热点|cpu|性能)",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?:扫整表|扫全表|全表扫描|full[- ]table\s+scan|full[- ]scann\w*)"
        r".{0,80}(?:收拾|弄快|变快|提速|优化|改|stop|fast)|"
        r"(?:make|弄|整).{0,80}(?:queries?|查询).{0,40}(?:fast|快)",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?:\b(?:queries?|requests?|apis?)\b|查询|接口).{0,48}"
        r"(?:takes?\s*\d+(?:\.\d+)?\s*[sm]|慢|磨磨唧唧|越跑越肉)"
        r".{0,80}(?:make|弄|整|优化|提速).{0,24}(?:fast|快|利索|正常)?",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:refactor|refacor|optimi[sz]e|tune)\b.{0,80}"
        r"\b(?:sql|database|queries?|indexes?|access\s+paths?)\b|"
        r"\b(?:sql|database|queries?|indexes?)\b.{0,80}"
        r"\b(?:refactor|refacor|optimi[sz]e|tune|speed\s+up|make\s+it\s+fast)\b|"
        r"\b(?:sql\s+)?(?:slow\s+)?quer(?:y|ies)\b.{0,48}"
        r"\b(?:fix(?:ed)?|faster|fast)\b|"
        r"\b(?:make|help)\b.{0,48}\b(?:queries?|sql)\b.{0,48}"
        r"\b(?:fast|faster)\b",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:make\s+(?:it\s+)?stop|stop)\b.{0,64}"
        r"\bfull[- ]scann\w*\b|"
        r"\b(?:refactor|refacor)\b.{0,64}"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h)\b",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
]

_EXPLAIN_CONTEXT_TERMS = (
    r"code|file|files|function|method|class|module|architecture|system|project|"
    r"agent|skill|memory|tool|flow|logic|design|routing|pipeline|api|repo|"
    r"repository|codebase|implementation|auth|authentication|login|test|"
    r"script|handler|retry|permission|token"
)

_EXPLAIN_PATTERNS = [
    (
        r"^\s*(?:请\s*)?(?:只|仅)?\s*"
        r"(?P<action_described_goal>解释|说明)(?:一?下)?"
        r"[^,，。！？;；\n]{0,24}(?:命令|代码|脚本|建议|方案|操作)"
        r"[^,，。！？;；\n]{0,32}(?:为什么|为何|希望|打算|试图|旨在)"
        r"[^,，。！？;；\n]{0,48}(?:什么|哪些|问题|文件)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?P<action_explain_hypothetical>只讲(?:一?下)?|解释|说明)"
        r"[^。！？!?;；\n]{0,40}(?:如果|假如|要是)[^。！？!?;；\n]{0,32}"
        r"(?:修改|更新|重写|修复|删除|重命名)\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b|"
        r"\b(?P<action_why_effect>why\s+would\s+(?:someone|anyone|we|you))\b"
        r"[^.!?;\n]{0,48}\b(?:patch|rewrite|modify|update|change|edit|"
        r"delete|remove|rename|run|execute)\b[^.!?;\n]{0,32}"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b|"
        r"\b(?P<action_show_how>show\s+me\s+how\s+to)\s+"
        r"(?:patch|rewrite|modify|update|change|edit|delete|remove|rename|"
        r"run|execute)\b[^.!?;\n]{0,32}"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"\b(?P<action_explanation_of>(?:i\s+want\s+)?an?\s+explanation\s+of)"
        r"[^.!?;\n]{0,48}\b(?:pytest|tests?|test\s+suite|running|executing)\b|"
        r"\b(?P<action_compare_ways>(?:could\s+you\s+)?compare\s+"
        r"(?:the\s+)?ways?\s+to)\s+"
        r"(?:patch|rewrite|modify|update|change|edit)\b[^.!?;\n]{0,32}"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        # Effectful verbs can be the *subject* of an explanation.  Keep a
        # read-only explanation lane; the discussed-action guards below
        # suppress the nested mutation/execution candidate.
        r"^\s*(?:请|麻烦)?\s*(?:(?:我)\s*)?(?:只是|只|仅)?\s*"
        r"(?P<action_effect_subject>解释|说明|讲讲|讲解|分析|想知道)"
        r"[^,，。！？;；\n]{0,32}"
        r"(?:修改|更新|改|重写|删除|删|创建|新增|重命名|移动|执行|运行|跑|"
        r"patch|rewrite|update|change|delete|remove|create|rename|move|"
        r"run|execute)"
        r"[^,，。！？;；\n]{0,40}"
        r"(?:[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)|pytest|测试|单测)\b"
        r"(?:[^,，。！？;；\n]{0,40}(?:含义|意思|步骤|影响|会怎样|会发生什么|"
        r"means?|meaning|steps?|impact|affect))?",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"^\s*(?:please\s+)?(?:only\s+|just\s+)?"
        r"(?P<action_effect_subject_en>explain|describe|analy[sz]e|tell\s+me)"
        r"[^.!?;\n]{0,40}\b(?:steps?\s+to|what|why|how)\b"
        r"[^.!?;\n]{0,32}\b(?:rewrite|update|change|delete|remove|create|"
        r"rename|move|run|execute)\b"
        r"[^.!?;\n]{0,40}(?:\bpytest\b|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        # A why/how question can quote a mutation verb as its subject without
        # authorizing that mutation.  Keep the explanation operation while
        # ``_is_discussed_mutation`` suppresses the nested write candidate.
        r"^\s*(?:(?:我)\s*)?(?:只是|只|仅)?\s*"
        r"(?P<action_complement>解释|说明|讲(?:讲|解)?|想知道)"
        r"(?:一?下)?(?:而已)?\s*[:：]?\s*"
        r"(?:为什么|为何|怎么|怎样|如何|含义|意思|步骤|影响)"
        r"[^,，。！？;；\n]{0,32}"
        r"(?:修改|更新|改|删除|删|创建|新增|重命名|移动|"
        r"patch|update|change|delete|remove|create|rename|move)"
        r"[^,，。！？;；\n]{0,32}"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|"
        r"md|json|yaml|yml|toml)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:我|这里|这儿|当前|现在)?\s*(?:只|仅)\s*"
        r"(?P<action_summary>要|需要|想要|想看)?\s*"
        r"(?:一份|一个|个)?\s*(?:摘要|概述|总结)|"
        r"\b(?:i\s+)?(?:only|just)\s+(?:want|need)\s+"
        r"(?P<action_summary_en>(?:an?\s+)?(?:summary|overview))\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:只|仅)\s*(?P<action_anaphor>解释|说明|分析)(?:一?下)?\s*"
        r"(?:它|这(?:条|段|句|个)?|该(?:命令|内容)?|上面(?:这条)?)"
        r"的?(?:含义|意思|作用)|"
        r"\b(?:only|just)\s+(?P<action_anaphor_en>explain|describe)\s+"
        r"(?:what\s+)?(?:it|this|that)(?:\s+command)?\s+"
        r"(?:means?|does)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        # A terse rejection of explanation can precede a real mutation with
        # no punctuation (``别讲大道理直接把 README ...``).  Model it as
        # its own read-only denial so its polarity cannot bleed into the
        # following operation.
        r"(?P<action_meta>讲|解释|说明)"
        r"(?:大道理|空话|套话|原因|原理|过程|细节)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:只|仅)?\s*基于[^,，。！？;；\n]{0,32}"
        r"(?:日志|logs?)[^,，。！？;；\n]{0,24}"
        r"(?P<action_grounded>解释|说明|分析)"
        r"[^,，。！？;；\n]{0,24}"
        r"(?:启动|耗时|慢|错误|报错|失败|原因)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:pytest[^,，。！？;；\n]{0,24})?"
        r"(?:这条|该条|这个)?(?:命令|参数)"
        r"[^,，。！？;；\n]{0,20}"
        r"(?P<action_meaning>怎么理解|如何理解|什么意思|什么含义)|"
        r"(?P<action_parameters>讲|解释|说明)(?:一?下|清楚)?"
        r"[^,，。！？;；\n]{0,16}(?:每个|各个|逐个)参数|"
        r"(?:每个|各个|逐个)参数[^,，。！？;；\n]{0,16}"
        r"(?P<action_parameters_tail>讲|解释|说明)(?:一遍|一?下)?",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?P<action_why>(?:我)?(?:只)?想知道)"
        r"[^,，。！？;；\n]{0,24}(?:pytest|单测|测试)"
        r"[^,，。！？;；\n]{0,24}(?:为什么|为何|为啥)"
        r"[^,，。！？;；\n]{0,16}(?:加|用|带)?\s*-[a-z]",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:只|仅)\s*(?P<action>说明|解释|分析|判断)\s*"
        r"(?:一?下)?\s*(?:这段|该段|上面)?\s*(?:意图|intent)|"
        r"(?:只|仅)\s*(?P<action_result>分析|判断|说明).{0,24}"
        r"(?:会|将)?(?:被)?识别成.{0,16}\bintent\b|"
        r"\b(?:only|just)\s+"
        r"(?P<action_en>explain|describe|analy[sz]e)\b.{0,24}"
        r"\b(?:intent|request|example)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"^\s*(?:请|麻烦)?\s*"
        r"(?:(?P<action>说说|讲讲|解释|说明|告诉我|教我|"
        r"请问|想知道)[^。！？!?;；\n]{0,24})?"
        r"(?:(?:我|我们|咱们)\s*)?(?:该\s*)?"
        r"(?<!不管)(?<!无论)(?:怎么|怎样|如何)"
        r".{0,24}(?:跑|运行|执行).{0,24}"
        r"(?:pytest|(?:单元)?测试|单测)|"
        r"\b(?P<action_en>"
        r"(?:(?:explain|describe|tell\s+me)\s+)?how\s+"
        r"(?:(?:do|can|should|would)\s+(?:i|we|you)|"
        r"(?:i|we|you)\s+(?:can|should|would)|to)"
        r")\s+[^.!?;\n]{0,24}?\b(?:run|execute)\b"
        r".{0,24}\b(?:pytest|tests?)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:\bintent\b|意图)[^。！？!?;；\n]{0,40}"
        r"(?:\bcapabilit(?:y|ies)\b|能力)[^。！？!?;；\n]{0,40}"
        r"(?:\bskills?\b|技能)[^。！？!?;；\n]{0,40}"
        r"(?:映射|路由|链路)[^。！？!?;；\n]{0,24}"
        r"(?P<action>怎么走|如何走|怎么流转|解释|说明)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:pytest|测试命令|单测命令|测试参数)"
        r"[^,，。！？;；\n]{0,32}"
        r"(?P<action>解释清楚|讲清楚|说明白|讲明白|解释|说明)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:先\s*)?(?P<action>讲)(?:一?下)?[^,，。！？;；\n]{0,32}"
        r"(?:minicode|memory|agent[_ ]?loop|router|parser|代码|模块|流程)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:代码|模块|函数|类|系统).{0,24}"
        r"(?:只|先)?(?P<action>讲)(?:一?下|清楚)?"
        r".{0,32}(?:流程|链路|调用|数据流)|"
        r"(?:只|先)?(?P<action_first>讲)(?:一?下|清楚)?"
        r".{0,32}(?:代码|模块|函数|类|系统).{0,24}"
        r"(?:流程|链路|调用|数据流)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?P<action>讲清(?:楚)?).{0,60}"
        r"(?:数据|状态|请求|参数).{0,32}"
        r"(?:怎么|如何)?(?:走|流转|传递|变化)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    # An explain-style verb alone is not enough evidence — "what is the
    # weather" and "tell me a joke" match the bare verb just as well as a
    # real code question. Require a code/project-shaped noun (or a bare
    # filename) nearby so unrelated small talk stays unknown/low-confidence.
    (
        rf"(?:explain|explian|describe|tell(?!\s+me\s+(?:if|whether)\b)|"
        rf"what is|how to|how does)\b.{{0,80}}"
        rf"(?:\b(?:{_EXPLAIN_CONTEXT_TERMS})\b|\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        (
            r"\btrace\b.{0,100}\b(?:prompts?|requests?|inputs?|events?)\b"
            r".{0,100}\b(?:models?|agents?|runtimes?|handlers?|apis?)\b"
        ),
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        (
            r"\b(?:teach|walk)\b.{0,100}"
            rf"(?:\b(?:minicode|{_EXPLAIN_CONTEXT_TERMS})\b|"
            r"\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h))"
        ),
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        (
            r"(?:带我|帮我|请).{0,16}(?:走一遍|过一遍|学习|理解)"
            r".{0,100}(?:启动流程|调用流程|执行流程|调用链|"
            r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs))"
        ),
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"\b(?:show|display)\b.{0,80}\b"
        r"(?:implementations?|source\s+code|code|files?|modules?)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:解释|说明|讲解|分析).{0,80}"
        r"(?:代码|架构|流程|调用|工具|系统|项目|技能|记忆|函数|模块|文件|实现|逻辑|设计|原因|作用)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:给我|提供|附上).{0,60}"
        r"(?:代码|架构|流程|调用|工具|系统|项目|技能|skill|路由|函数|模块|文件|实现|逻辑|设计)"
        r".{0,40}(?:说明|解释|介绍|讲解)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:解释|说明|梳理|讲清楚|讲清).{0,120}"
        r"(?:调用链|调用路径|调用过程|链路|主链路|状态流|数据流|组件层级|"
        r"任务分配|重试|汇总|架构|模块关系|执行路径)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:调用链|调用路径|调用过程|链路|主链路|状态流|数据流|组件层级|"
        r"任务分配|重试|汇总|架构|模块关系|执行路径).{0,80}"
        r"(?:解释|说明|梳理|讲清楚|讲清)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:怎么|如何).{0,100}(?:一路|流转|传到|走到|调用).{0,80}"
        r"(?:函数|方法|角色|存储|数据库|模块|组件)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"\b(?:trace|find|show)\b.{0,80}\b(?:callers?|references?|"
        r"classes?|modules?)\b.{0,80}\b(?:functions?|methods?|calls?)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:捋顺|捋清|捋捋|捋一下|讲讲|说说|说明白).{0,100}"
        r"(?:代码|实现|调用|流程|链路|权限|角色|token|auth)|"
        r"(?:代码|实现|调用|流程|链路|权限|角色|token|auth).{0,100}"
        r"(?:捋顺|捋清|捋捋|捋一下|讲讲|说说|说明白)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:流程|链路|路径|判断|代码).{0,60}(?:讲清|说明白|讲明白)|"
        r"(?:讲清|说明白|讲明白).{0,60}(?:流程|链路|路径|判断|代码)",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:解释|说明|讲讲|讲解).{0,48}"
        r"\b(?:handler|script|api|router|parser|token|permission)\b",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:只|仅)?\s*+(?:给我|给出|提供)?\s*+(?:一个|一份)?\s*+"
        r"口头(?:概要|概述|总结|说明)|"
        r"(?:只|仅)?\s*+口头(?:说|讲|解释)(?:一?下)?",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
]

_SEARCH_PATTERNS = [
    (
        r"(?P<action_read_implementation>阅读)\s*"
        r"(?:现有|当前|已有|现行)?\s*"
        r"(?:[^,，。！？;；\n]{1,48}的)?(?:代码|实现|源码)",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"\b(?:rather\s+than\s+continuing\s+(?:that|it)\s*[,，]?\s*)?"
        r"(?P<action_read_file>read)\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b|"
        r"\b(?P<action_pivot>pivot\s+to)\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b|"
        r"(?:把)?(?:目标|焦点|注意力)\s*(?P<action_switch_simple>换成|换到|"
        r"转到|切到)\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b|"
        r"(?P<action_read_zh>(?:现在|当前|暂时)?\s*(?:只|仅)?\s*"
        r"阅读)\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?P<action_direct_view>\b(?:view|focus\s+on)\b)\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b|"
        r"^\s*(?P<action_preferred_file>[\w/\\._-]+\."
        r"(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))"
        r"\s+rather\s+than\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?P<action_only_look>(?:只|仅)\s*看)(?:一?下)?\s*"
        r"风险",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        rf"(?P<action>(?:改{_VIEW_SWITCH_AFTER_CHANGE_PATTERN}|"
        rf"(?:换|转)(?:到|去|成)?\s*{_VIEW_SWITCH_MODIFIER_ZH_PATTERN}"
        rf"{_VIEW_SWITCH_VERB_PATTERN}(?:一?下)?))\s*"
        r"(?:另一个文件|文件|readme|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml))",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?P<action_focus_zh>(?:把|将)?\s*(?:视线|注意力|焦点)\s*"
        r"(?:转|移|切|换)(?:到|向))\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        rf"(?P<action_switch>\b{_ENGLISH_VIEW_SWITCH_PREFIX_PATTERN}\b)"
        r"\s*(?:(?:only|just)\s+)?(?:inspect|review|check|read|"
        r"analy[sz]e|look\s+at)\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)|"
        rf"(?P<action_switch_target>\b{_ENGLISH_VIEW_SWITCH_PREFIX_PATTERN}\b)\s*"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)|"
        r"(?P<action_instead>\b(?:look\s+at|read)\b)\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\s+(?:instead\b|rather\s+than\s+"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\b)|"
        r"^\s*(?P<action_bare_instead>[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|"
        r"go|rs|cpp|c|h|md|json|yaml|yml|toml))\s+instead\b|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
        r"json|yaml|yml|toml)\s+instead\s*[,，]?\s*"
        r"(?P<action_postfix>inspect|review|check|read|analy[sz]e)"
        r"(?:\s+it)?\b",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?:只|仅)?(?P<action>列(?:出)?|罗列)"
        r"[^,，。！？;；\n]{0,40}(?:风险|引用|imports?|references?)",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        (
            r"(?:\b(?:search|look\s+up|find)\b|(?:搜(?:索)?|查(?:找)?))"
            r".{0,60}(?:\b(?:news|headlines?|weather|restaurants?|attractions?)\b|"
            r"新闻|资讯|热点|天气|餐厅|景点)"
        ),
        IntentType.SEARCH,
        ActionType.READ,
    ),
    # "search FOR the function" is the idiomatic English phrasing — the
    # bounded gap covers the preposition/determiner between verb and noun.
    (
        r"\b(?:search|find|locate|lookup)\b.{0,20}\b(?:files?|codes?|functions?|classes?|variables?|references?)\b",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"\b(?:search|find|locate|trace)\b.{0,60}\b(?:definitions?|defined|callers?|usages?|invocations?|call sites?)\b",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?:搜索|查找|(?<!审)(?<!检)(?<!排)(?<!核)(?<!复)(?<!调)"
        r"查(?!询)(?:一?下)?|定位|找出|找).{0,60}"
        r"(?:文件|代码|函数|类|变量|引用|调用|定义|谁调用|调用点|\w+_\w+|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?:项目|仓库|代码库|repo|repository|codebase).{0,80}"
        r"(?:包含|包括|有|由)?(?:哪些|什么)(?:代码)?模块",
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        r"(?:谁|哪个|哪段).{0,40}(?:拼出|拼接|生成|构造|写出|打印)"
        r".{0,40}(?:这句|消息|日志|字符串)|"
        r"(?:这句|消息|日志|字符串).{0,40}(?:谁|哪个|哪段).{0,40}"
        r"(?:拼出|拼接|生成|构造|写出|打印)",
        IntentType.SEARCH,
        ActionType.READ,
    ),
]

_DELETE_PATTERNS = [
    (
        r"\b(?:delete|remove)\b.{0,40}(?:files?|codes?|modules?|classes?|"
        r"\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.CODE,
        ActionType.DELETE,
    ),
    (
        r"(?:删除|移除|删掉|删(?!改)).{0,40}(?:文件|代码|模块|类|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.CODE,
        ActionType.DELETE,
    ),
]

_MOVE_PATTERNS = [
    (
        # Two explicitly named files make the colloquial destination verb a
        # bounded file operation; filename recognition is shared downstream.
        r"(?:把|将)\s*[\w/\\.-]+\.[a-z0-9]{1,10}\s*"
        r"(?P<action_path_move>移到|挪到|移动到)\s*"
        r"[\w/\\.-]+\.[a-z0-9]{1,10}(?![a-z0-9_])",
        IntentType.CODE,
        ActionType.MOVE,
    ),
    (
        r"\b(?P<action>move)\b.{0,40}\b(?:files?|modules?|packages?)\b"
        r".{0,48}\b(?:update|change|fix)\b.{0,24}"
        r"\b(?:imports?|refs?|references?)\b",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        r"(?:不要|不用|不必|无需|无须|别|甭|不)?\s*"
        r"(?P<action>挪|移动).{0,32}(?:文件|目录|模块)|"
        r"\b(?P<action_en>move)\b.{0,32}\b(?:files?|directories|modules?)\b",
        IntentType.CODE,
        ActionType.MOVE,
    ),
    (
        r"(?:文件|目录|模块|包)[^,，。！？;；\n]{0,12}"
        r"(?:不要|不用|不必|无需|无须|别|甭|不)?\s*"
        r"(?P<action_tail>挪|移动)",
        IntentType.CODE,
        ActionType.MOVE,
    ),
]

_CONTEXT_ONLY_RENAME_PATTERN = (
    r"^\s*(?:就按|按|照).{0,16}(?:上面|刚才|之前|前面).{0,16}"
    r"(?P<action>改名|重命名)\s*[。.!！]?\s*$"
)


_RENAME_PATTERNS = [
    (
        r"\b(?P<action>rename)\b.{0,32}(?:文件|模块|包|目录|类|函数|方法)",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        r"(?P<source>[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h))"
        r"\s*(?P<action>[-=]?>|→|⟶|➜)\s*"
        r"(?P<target>[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h))",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        r"(?:包名|模块名|类名|函数名).{0,24}"
        r"(?P<action>换掉|换名|改名|改掉|重命名)|"
        r"(?:把|将).{0,48}(?:[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h)|"
        r"文件|模块|类|函数).{0,24}"
        r"(?P<action_tail>改名|重命名)(?!\s*(?:逻辑|规则|处理|实现|代码))",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (_CONTEXT_ONLY_RENAME_PATTERN, IntentType.CODE, ActionType.RENAME),
    (
        r"\b(?:rename)\b.{0,60}(?:files?|modules?|packages?|directories?|folders?|"
        r"symbols?|classes?|functions?|methods?|"
        r"\w+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        r"\bmove\b.{0,60}\b(?:files?|modules?|packages?|directories?|folders?)\b",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        r"(?:重命名|改名).{0,60}(?:文件|模块|类|函数|方法|"
        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))",
        IntentType.CODE,
        ActionType.RENAME,
    ),
]

_COMPARE_PATTERNS = [
    (
        r"(?P<action_literal>比较|对比)\s*"
        r"(?:字符串|文本|字段|枚举(?:值)?|术语|名称)"
        r"[^。！？!?;；\n]{0,96}(?:含义|意思|区别|差异)|"
        r"\b(?P<action_literal_en>compare)\b[^.!?;\n]{0,96}"
        r"\b(?:meanings?|semantics?)\b",
        IntentType.QUESTION,
        ActionType.COMPARE,
    ),
    (
        r"(?:两|两个|俩|这俩|那俩).{0,24}(?:实现|方案|设计)"
        r".{0,32}(?:优缺点|取舍|差异).{0,20}"
        r"(?P<action>捋一?下|比较|对比|看看)?",
        IntentType.REVIEW,
        ActionType.COMPARE,
    ),
    (
        r"\bcompare\b.{0,60}\b(?:codes?|files?|implementations?|designs?|approaches?)\b",
        IntentType.REVIEW,
        ActionType.COMPARE,
    ),
    (
        (
            r"(?:比较|对比).{0,60}"
            r"(?:代码|文件|实现|设计|方案|摘要|约束|结果|差异|保真|"
            r"行程|路线|酒店|餐厅|旅游计划|旅行计划)"
        ),
        IntentType.REVIEW,
        ActionType.COMPARE,
    ),
]

_REVIEW_PATTERNS = [
    (
        r"(?:登录|鉴权|认证|授权|权限)"
        r"[^,，。！？;；\n]{0,40}"
        r"(?P<action_postfix_security_review>(?:再|重新)?\s*审(?:一遍|一?下)?)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:只|仅|还是)?\s*(?P<action_short>审)(?:一?下|下)?\s*"
        r"(?:[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h)|"
        r"auth|authentication|代码|模块|实现|权限|安全)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:还是|继续|那就)\s*(?P<action>审)(?=$|[,，。！？;；])",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>检查|审查|审核|审计)"
        r"[^,，。！？;；\n]{0,64}(?:\bintent\b|意图)"
        r"[^,，。！？;；\n]{0,48}(?:\bcapabilit(?:y|ies)\b|能力)"
        r"[^,，。！？;；\n]{0,48}(?:定义|映射|注册)"
        r"[^,，。！？;；\n]{0,24}(?:重复|冲突|遗漏|一致)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>审(?:查|核|计|一?下|下)?)"
        r"[^,，。！？;；\n]{0,32}(?:依赖|包|版本)"
        r"[^,，。！？;；\n]{0,24}(?:风险|兼容|安全)|"
        r"(?:依赖|包|版本)[^,，。！？;；\n]{0,24}(?:风险|兼容|安全)"
        r"[^,，。！？;；\n]{0,16}(?P<action_tail>审|审查|审核|审计|检查)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:只读|read[- ]only)\s*(?P<action>审计|审查|审核|review|audit)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:这|那|本)?(?:批)?(?:改动|变更|差异|diff)"
        r".{0,24}(?P<action>看看|看一?下|过一?眼|审一?下)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>看|审(?:一?下|下)?|review|check)"
        r"[^,，。！？;；\n]{0,16}\b(?:git\s+)?diff\b|"
        r"(?P<action_pin>审(?:一?下|下)?|检查|review|check)"
        r"[^,，。！？;；\n]{0,24}\b(?:current\s+)?pin\b"
        r"(?!\s*(?:码|code|number|password)(?:\b|$))",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>审(?:一遍|一?下|查|核|计)?)\s*"
        r"(?:credentials?|密钥|凭据|令牌).{0,32}"
        r"(?:leaks?|泄漏|外泄|暴露)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>看).{0,20}(?:diff|差异).{0,48}"
        r"(?:secret|密钥|泄漏|敏感信息)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>审)(?:一遍|一?下)?.{0,24}版本"
        r".{0,24}(?:过期|陈旧|落后)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:注入|提示词|安全|越权).{0,32}"
        r"(?P<action>审计|审查|审核)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?P<action>审)(?:一遍|一?下)?\s*.{0,48}"
        r"(?:auth(?:entication)?|权限|鉴权|认证).{0,32}"
        r"(?:bypass|绕过|越权|漏洞)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"\breview\b.{0,80}\b(?:commits?|changes?|diffs?|staged|staging)\b",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        (
            r"\b(?:look\s+at|inspect|review)\b.{0,64}"
            r"\b(?:modules?|code|implementation|design|architecture)\b"
            r".{0,64}\b(?:problems?|issues?|flaws?|smells?)\b"
        ),
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        (
            r"\b(?:is|are)\s+there\b.{0,64}"
            r"\b(?:inconsisten(?:t|cies)|unused|dead\s+code|"
            r"hardcoded\s+secrets?|injection\s+risks?|security\s+problems?)\b"
            r".{0,64}\b(?:code|modules?|implementation|here)\b|"
            r"\b(?:is|are)\s+there\b.{0,64}\b(?:code|modules?)\b"
            r".{0,64}\b(?:inconsisten(?:t|cies)|unused|dead\s+code|"
            r"hardcoded\s+secrets?|injection\s+risks?)\b"
        ),
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?:analyze|analyse|evaluate|assess)\b.{0,60}\b(?:codes?|files?|implementations?|designs?|architectures?|modules?|systems?|projects?)\b",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?:identify|assess|analyze)\b.{0,80}\b(?:risks?|issues?|problems?|regressions?)\b",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:代码|实现|设计|架构|模块|skill|路由).{0,30}(?:审查|评审|检查)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:代码|文件|实现|设计|架构|系统|项目|技能|skill|路由|模块)"
        r".{0,30}(?:评价|评估|点评|审视)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:评价|评估|点评|审视|看(?:一)?看).{0,80}"
        r"(?:代码|文件|实现|设计|架构|系统|项目|技能|skill|路由|模块)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:review|check|audit|inspect).{0,80}"
        r"(?:code|file|implementation|design|architecture|system|project|memory|"
        r"skill|routing|context|compaction|summary|authentication|permission|"
        r"secret|security|performance|latency|profiling|database|query|sql|pr|pull request)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:审查|检查|审核|评审|审计).{0,80}"
        r"(?:代码|文件|实现|设计|架构|系统|项目|记忆|内存|技能|skill|路由|"
        r"上下文|压缩|摘要|登录|鉴权|权限|密钥|泄漏|安全|性能|延迟|"
        r"数据库|查询|索引|越权|风险|所有权|偏好|召回|来源链|持久化|"
        r"p95|回归)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:安全|权限|越权|鉴权|认证|密钥|漏洞).{0,50}"
        r"(?:审查|检查|审核|评审|审计)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:核对|复核|查清|确认|调查).{0,100}"
        r"(?:召回|来源|来源链|持久化|记忆|偏好|摘要|压缩|保真|约束|遗漏|"
        r"禁止事项|越权|权限|风险|安全|所有权)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"\b(?:profile|benchmark)\b.{0,80}\b(?:cpu|memory|latency|"
        r"throughput|performance|regressions?)\b",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:分析|剖析|测量).{0,80}(?:cpu|性能|延迟|吞吐|回归)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:瞅瞅|瞄一眼|看(?:一)?看|过一眼).{0,100}"
        r"(?:越权|权限|安全|风险|漏洞|坑|毛病|合理|设计|代码|实现)|"
        r"(?:越权|权限|安全|风险|漏洞|登录|管理员|普通人).{0,100}"
        r"(?:瞅瞅|瞄一眼|看(?:一)?看|过一眼|摸到)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?:\blook\s+for\b|\bcheck\b).{0,80}"
        r"(?:auth(?:entication)?|permission|privilege).{0,40}"
        r"(?:bypass|escalation|risk)?|"
        r"(?:审|审一?下|审吧).{0,80}(?:安全|权限|越权|鉴权)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
]

_TEST_PATTERNS = [
    (
        r"\b(?P<action_direct_runner>start|launch|run|execute|running|"
        r"executing|launching|starting)\b"
        r"[^.!?;\n]{0,40}\b(?:pytest|checks?|test\s+suite|tests?)\b|"
        r"(?P<action_zh_runner>重新?\s*(?:执行|运行|启动|跑)|运行|执行|"
        r"启动|跑(?!起来))"
        r"[^,，。！？!?;；\n]{0,48}"
        r"(?:pytest|checks?|(?:acceptance|browser|contract|e2e|integration|"
        r"load|performance|regression|security|slow|smoke|system|unit)\s+"
        r"(?:tests?|suite)|tests?|测试|单测|用例)|"
        r"(?:pytest|(?:acceptance|browser|contract|e2e|integration|load|"
        r"performance|regression|security|slow|smoke|system|unit)\s+tests?|"
        r"tests?|测试|单测|用例)\s*"
        r"(?P<action_post_runner>启动|开启|运行|执行|跑(?!起来))"
        r"(?:一?下|一遍)?|"
        r"(?P<action_deictic_runner>(?:现在|马上|立即)\s*(?:执行|运行|跑))"
        r"(?=\s*(?:$|[,，。！？!?;；]))",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:不需要|无需|无须|不必)\s*"
        r"(?P<action_unneeded_cases>(?:回归|单元|集成)?测试用例|测例|用例)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\b(?:tests?|test\s+cases?)\s+(?:must\s+|should\s+)?neither\s+"
        r"(?:be\s+)?(?P<action_passive_neither_create>created|written|added)"
        r"\s+nor\s+(?:be\s+)?(?:run|executed)\b|"
        r"\bneither\s+(?P<action_object_neither_create>create|add|write)\s+"
        r"(?:pytest\s+cases|tests?|test\s+cases?)\s+nor\s+"
        r"(?:run|execute)\s+(?:them|those|the\s+tests?)\b|"
        r"\b(?:leave|leaving)\s+(?:the\s+)?(?:tests?|test\s+suite)\s+"
        r"(?P<action_leave_unwritten>unwritten)\b|"
        r"\bno\s+(?:new|additional|extra)?\s*"
        r"(?:(?:regression|unit|integration)\s+)?"
        r"(?P<action_no_typed_cases>tests?|cases?|test\s+cases?)\b|"
        r"(?:不要|别|不许|不得|不准|不允许)\s*"
        r"(?P<action_front_create_any>新建|新增|添加|创建|编写|"
        r"补(?!跑|充说明)|写|加|添)\s*(?:任何|新的|新)?\s*"
        r"(?:回归|单元|集成)?\s*(?:测试|单测|测例|用例)|"
        r"(?:测试|单测|测例|用例)(?:代码|文件|用例)?\s*"
        r"(?:不要|别|不许|不得|不准|不允许)\s*"
        r"(?P<action_artifact_denied>动|改|修改|变更|碰)|"
        r"(?:测试|单测|测例|用例)\s*(?:一条|一行|一个)?\s*不\s*"
        r"(?P<action_counted_create>写|加|添|补|创建|新增)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\b(?:tests?|test\s+cases?)\s+(?:must\s+|should\s+)?neither\s+"
        r"(?:be\s+)?(?:created|written|added)\s+nor\s+(?:be\s+)?"
        r"(?P<action_passive_neither_execute>run|executed)\b|"
        r"\bneither\s+(?:create|add|write)\s+"
        r"(?:pytest\s+cases|tests?|test\s+cases?)\s+nor\s+"
        r"(?P<action_object_neither_execute>run|execute)\s+"
        r"(?:them|those|the\s+tests?)\b|"
        r"\b(?:leave|leaving)\s+(?:the\s+)?"
        r"(?:pytest|tests?|test\s+suite)\s+"
        r"(?P<action_leave_unexecuted>unrun|unexecuted|not\s+run)\b|"
        r"\b(?:leave|leaving)\s+(?:the\s+)?(?:tests?|test\s+suite)\s+"
        r"unwritten\s+and\s+(?P<action_leave_both_unexecuted>unexecuted)\b|"
        r"\b(?:tests?|test\s+suite)\s+(?:must|should|can|may)\s+"
        r"(?P<action_passive_never>never\s+(?:be\s+)?(?:run|executed)|"
        r"not\s+(?:be\s+)?(?:run|executed))\b|"
        r"\b(?:without|skip|avoid)\s+"
        r"(?P<action_scoped_gerund>running|executing)\s+"
        r"(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
        r"load|performance|regression|security|slow|smoke|system|unit)\s+"
        r"(?:tests?|suite)\b|"
        r"(?:跳过|避开)\s*(?:验收|浏览器|契约|端到端|集成|负载|性能|"
        r"安全|慢速|耗时|冒烟|回归|系统|单元)\s*"
        r"(?P<action_scoped_skip_zh>测试|单测|测例|用例)|"
        r"(?:测试|单测|测例|用例)\s*(?:一条|一行|一个)?\s*不\s*"
        r"(?:写|加|添|补|创建|新增)\s*[,，、]\s*"
        r"(?:一遍|一次|一回)?\s*不\s*"
        r"(?P<action_counted_execute>跑|执行|运行)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\bneither\s+"
        r"(?P<action_neither_create>add|create|write)\s+nor\s+"
        r"(?:run|execute)\s+(?:pytest\s+cases|tests?|test\s+cases?)\b|"
        r"\b(?:tests?|pytest\s+cases)\s*:\s*neither\s+"
        r"(?P<action_neither_subject_create>add|create|write)\s+nor\s+"
        r"(?:run|execute)\b|"
        r"\bno\s+test\s+(?P<action_no_creation>creation)\s+and\s+"
        r"no\s+test\s+execution\b|"
        r"\bskip\s+(?P<action_skip_create>adding|creating|writing)\s+"
        r"tests?\s+and\s+skip\s+running\s+(?:them|tests?)\b|"
        r"\bdo\s+not\s+(?P<action_do_not_create>create|add|write)\s+"
        r"or\s+(?:run|execute)\s+tests?\b|"
        r"\bwithout\s+(?P<action_without_create>writing|creating|adding)\s+"
        r"tests?\s+or\s+running\s+(?:them|tests?)\b",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\bneither\s+(?:add|create|write)\s+nor\s+"
        r"(?P<action_neither_execute>run|execute)\s+"
        r"(?:pytest\s+cases|tests?|test\s+cases?)\b|"
        r"\b(?:tests?|pytest\s+cases)\s*:\s*neither\s+"
        r"(?:add|create|write)\s+nor\s+"
        r"(?P<action_neither_subject_execute>run|execute)\b|"
        r"\bno\s+test\s+creation\s+and\s+no\s+test\s+"
        r"(?P<action_no_execution>execution)\b|"
        r"\bskip\s+(?:adding|creating|writing)\s+tests?\s+and\s+"
        r"skip\s+(?P<action_skip_execute>running|executing)\s+"
        r"(?:them|tests?)\b|"
        r"\bdo\s+not\s+(?:create|add|write)\s+or\s+"
        r"(?P<action_do_not_execute>run|execute)\s+tests?\b|"
        r"\bwithout\s+(?:writing|creating|adding)\s+tests?\s+or\s+"
        r"(?P<action_without_execute>running|executing)\s+"
        r"(?:them|tests?)\b",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\bwithout\s+"
        r"(?P<action_coordinated_create>creating|adding|writing|modifying)"
        r"\s+(?:or|and)\s+(?:running|executing)\s+tests?\b|"
        r"(?:测试|单测|测例|用例)[^,，。！？;；\n]{0,12}"
        r"(?:既\s*)?不\s*"
        r"(?P<action_coordinated_create_zh>新增|添加|创建|编写|写|改)"
        r"\s*(?:也|且|又|并且?)\s*不\s*(?:执行|运行|跑)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\bwithout\s+(?:creating|adding|writing|modifying)"
        r"\s+(?:or|and)\s+"
        r"(?P<action_coordinated_execute>running|executing)\s+tests?\b|"
        r"(?:测试|单测|测例|用例)[^,，。！？;；\n]{0,12}"
        r"(?:既\s*)?不\s*(?:新增|添加|创建|编写|写|改)"
        r"\s*(?:也|且|又|并且?)\s*不\s*"
        r"(?P<action_coordinated_execute_zh>执行|运行|跑)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\bno\s+(?:test|pytest)\s+"
        r"(?P<action_nominal_runs>runs?|execution)\b",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\bno\s+(?:new|additional|extra)?\s*"
        r"(?P<action_nominal>tests?)\b|"
        r"\b(?:avoid|skip)\s+"
        r"(?P<action_avoid_create>creating|adding|writing|"
        r"changing|modifying)\s+(?:(?:any|the)\s+)?tests?\b|"
        r"\b(?P<action_avoid_modifications>avoid(?:ing)?)\s+"
        r"(?:any\s+)?(?:changes?|modifications?|edits?)\s+to\s+"
        r"(?:the\s+)?(?:test\s+files?|test\s+suite|tests?)\b|"
        r"\b(?:leave|leaving)\s+(?:the\s+)?"
        r"(?P<action_leave_untouched>test\s+files?|test\s+suite|tests?)\s+"
        r"(?:untouched|unchanged|unmodified)\b|"
        r"\b(?P<action_unchanged>tests?(?:\s+suite)?)\b.{0,24}"
        r"\b(?:must\s+)?(?:stay|remain)\s+"
        r"(?:untouched|unchanged|unmodified)\b|"
        r"(?P<action_artifact>测试(?:代码|文件|用例)?)"
        r"[^,，。！？;；\n]{0,12}(?:保持|维持)(?:原样|不变)|"
        r"(?P<action_artifact_untouched>测试(?:代码|文件|用例)?)"
        r"[^,，。！？;；\n]{0,12}"
        r"(?:不许|不得|不能|不要|别|不准|不允许)\s*"
        r"(?:动|改|修改|变更|碰)|"
        r"(?:集成|单元|冒烟|慢速|耗时|契约|端到端)\s*"
        r"(?P<action_scoped_create_front_zh>测试|用例)(?:先)?\s*"
        r"(?:不许|不得|不能|不要|别|不准|不允许)\s*"
        r"(?:写|加|添|新增|添加|创建|生成)|"
        r"(?:测试|用例)[^,，。！？;；\n]{0,12}"
        r"(?:不许|不得|不能|不要|别|不准|不允许)[^,，。！？;；\n]{0,8}"
        r"(?P<action_post>写|加|添|补(?!跑|充说明)|新增|添加|创建|生成)|"
        r"(?:测试|单测|测例|用例)(?:用例)?\s*"
        r"(?:不许|不得|不能|不要|别|不准|不允许|不)\s*"
        r"(?P<action_subject_create>补(?!跑|充说明)|写|加|添|新增|"
        r"添加|创建|生成)|"
        r"(?:不要|别|不许|不得|不准|不允许)\s*"
        r"(?P<action_front_create>新建|新增|添加|创建|编写)\s*"
        r"(?:回归|单元|集成|测试)?(?:测试|用例)|"
        r"\bnot\s+(?:the\s+)?"
        r"(?:integration|unit|smoke|contract|end[- ]to[- ]end|e2e)\s+"
        r"(?P<action_excluded_create>tests?)\b",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\bwithout\s+(?P<action_without>running|executing)\s+"
        r"(?:(?:any|the|all|every)\s+)?(?:(?:entire|whole)\s+)?"
        r"(?:test\s+suite|tests?|pytest)\b|"
        r"\bavoid\s+(?P<action_avoid_execute>running|executing)\s+"
        r"(?:(?:any|the|all|every)\s+)?(?:(?:entire|whole)\s+)?"
        r"(?:test\s+suite|tests?)\b|"
        r"\b(?:skip|avoid)\s+(?:the\s+|any\s+|all\s+|every\s+)?"
        r"(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
        r"load|performance|security|slow|smoke|system|unit)\s+"
        r"(?P<action_scoped>tests?|suite)\b|"
        r"\b(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
        r"load|performance|security|slow|smoke|system|unit)\s+"
        r"(?P<action_scoped_off_limits>tests?)\s+(?:are|is)\s+off[ -]?limits\b|"
        r"(?:跳过|避开)\s*(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|"
        r"慢速|耗时|冒烟|系统|单元)\s*"
        r"(?P<action_scoped_zh>测试|用例)|"
        r"(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|慢速|耗时|冒烟|"
        r"系统|单元)\s*"
        r"(?P<action_scoped_front_zh>测试|用例)(?:先)?\s*"
        r"(?:跳过|避开|(?:不许|不得|不能|不要|别|不准|不允许)\s*"
        r"(?:执行|运行|跑))|"
        r"(?:测试|单测|用例)[^,，。！？;；\n]{0,12}?"
        r"(?:也\s*)?(?:不要|别|甭|不用|不必|无需|无须|不许|不得|"
        r"不准|禁止|不能|不可|不允许)\s*"
        r"(?P<action_chained_denied_run>跑|运行|执行)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\bwithout\s+(?:(?:any|all|the)\s+)?"
        r"(?P<action_without_tests>tests?|test\s+suite)\b|"
        r"\b(?:skip|avoid)\s+(?:the\s+|any\s+|all\s+|every\s+)?"
        r"(?P<action_skipped_tests>tests?|test\s+suite)\b"
        r"(?!\s+(?:reports?|plans?|summaries|results?|documentation)\b)|"
        r"\b(?P<action_off_limits>tests?(?:\s+suite)?)\s+"
        r"(?:are|is)\s+(?:(?:completely|strictly|totally|entirely)\s+)?"
        r"off[ -]?limits\b|"
        r"(?P<action_mixed_off_limits>测试|单测|测例|用例)\s*"
        r"off[ -]?limits\b|"
        r"\b(?P<action_unrun>tests?(?:\s+suite)?)\b.{0,16}"
        r"\b(?:stay|remain)\s+(?:unrun|not\s+run|unexecuted)\b|"
        r"(?:跳过|略过)\s*(?:所有|全部|任何)?\s*"
        r"(?P<action_skipped_tests_zh>测试|单测|测例|用例)|"
        r"(?P<action_tests_off_zh>测试|单测|测例|用例)\s*"
        r"(?:都|全部|一律)?\s*(?:跳过|略过|不许执行|不得执行|禁止执行|"
        r"不能执行|不可执行|不要执行|别执行|不执行|不跑)|"
        r"(?:不|不要|别|无需|无须|不必|不需要)\s*(?:再\s*)?"
        r"(?P<action_compact_denied>跑测|复测)|"
        r"(?P<action_retest_denied>复测)\s*"
        r"(?:(?:这(?:一)?步|这(?:一)?环节)\s*)?(?:就\s*)?"
        r"(?:不必|无需|无须|不用|不要|不需要)(?:再)?(?:做|进行)?",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\bwithout\s+(?P<action_missing_tests>adding|writing|creating)"
        r"\s+(?:any\s+)?tests?\b",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?P<action_benchmark>基准(?:测|测试))(?:一?下)?"
        r".{0,48}(?:数据库|查询|接口|请求|服务|启动|命令|解析器|parser|"
        r"database|query|api|cache)"
        r".{0,48}(?:耗时|延迟|吞吐|瓶颈|慢|性能|latency|throughput|slow)?",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:pytest|测试|单测)(?:先)?(?:也)?\s*"
        r"(?:不要|别|甭|不用|不必|无需|无须|不许|不得|不准|禁止|"
        r"不能|不可|不允许)\s*"
        r"(?P<action_subject_run>跑|运行|执行)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:不要|别|无需|无须|不必|不)\s*"
        r"(?P<action_bare_execute>执行)"
        r"(?=\s*(?:$|[,，。！？!?;；]))",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?P<action_compact>跑测)(?:试)?"
        r"(?=\s*(?:$|[,，。！？!?;；]))",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        # Grammar-shaped TDD order cues.  These are intentionally narrower
        # than bare ``测试``/``测`` so status prose and quoted discussions do
        # not manufacture permission to create or execute tests.
        r"(?:先)?(?P<action_tdd>测试)(?=\s*(?:再|然后|随后)\s*实现\s*$)|"
        r"(?P<action_inverted>测先写)(?=\s*(?:[,，]|$))",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?P<action_test_first>测试先行)"
        r"(?=\s*(?:$|[,，。！？!?;；]))|"
        r"(?P<action_reproduce>复现)(?:成|为)"
        r"(?:测试|用例|回归(?:测试|用例)?)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?P<action_retest>复测)(?=\s*(?:$|[,，。！？!?;；]))",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:不用|不要|别|无需|无须|不必)\s*"
        r"(?:帮|替)?(?:我)?\s*(?P<action>测)(?:试)?|"
        r"(?:不要|别|无需|无须|不必)?\s*"
        r"(?P<action_start>启动|开启).{0,12}(?:测试|pytest)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:\bbugs?\b|缺陷|错位|删错|会删).{0,96}"
        r"(?P<action>regression)\s+(?:first|先)|"
        r"(?P<action_zh>复现).{0,48}(?:\bbugs?\b|缺陷|错位|删错)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?P<action>写|加|添|补)(?:一?下|一个)?(?:失败(?:测试|用例)|"
        r"回归(?:测试|用例)?)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?:把|将).{0,64}(?P<action>复现)(?:成|为)"
        r"(?:测试|用例|回归(?:测试)?)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\b(?:with|including)\b.{0,24}\b(?:test\s+coverage|tests?)\b",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\b(?:write|create|add|generate)\b.{0,24}"
        r"\b(?:pytest\s+cases|tests?|test cases?)\b"
        r"(?![-\s]+(?:(?:run|execution)[-\s]+)?"
        r"(?:plans?|reports?|spec(?:ification)?s?|"
        r"descriptions?|docs?|documentation)\b)",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?:写|编写|加|添|新增|添加|创建|生成|补充|补).{0,30}(?:测试|用例)"
        r"(?!(?:报告|计划|规范|说明|文档|方案|策略))",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"\b(?:test|verify|re[- ]?run|run|execute|start|launch)\b.{0,20}"
        r"\b(?:tests?|codes?|programs?|scripts?|cases?|(?:test\s+)?suite)\b",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\b(?:re[- ]?run|run|execute|rnu)\b.{0,20}"
        r"(?:pytest|测试|单测|用例)|"
        r"(?:pytest|测试|单测|用例)\s*(?:再\s*)?"
        r"(?:(?:可以|允许|照常?|照旧?)\s*)?"
        r"(?P<action_subject_execute>重跑(?:一遍|一下)?|跑(?:一遍|一下)?|"
        r"运行|执行)|"
        r"(?:pytest|tests?|test\s+suite)\s+"
        r"(?P<action_subject_rerun>again|re[- ]?run)\b",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:CI|持续集成)[^,，。！？!?;；\n]{0,12}"
        r"(?P<action_mixed_ci>run|execute)\s+(?:tests?|pytest|pyetst)\b",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        (
            r"\b(?:make\s+sure|ensure|confirm|verify)\b.{0,32}"
            r"\btests?\b.{0,24}\b(?:still\s+)?pass(?:es|ed|ing)?\b"
        ),
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:测试|单测|验证)(?![^,，。！？;；\n]{0,48}"
        r"(?:不要|不许|不得|不准|不允许|不能|别)\s*(?:再\s*)?"
        r"(?:跑|运行|执行|动|改|修改|变更|碰))"
        r".{0,48}(?:代码|程序|脚本|功能|接口|用例|pytest)|"
        r"(?:运行|执行).{0,48}(?:测试|单测|用例|pytest|命令)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"(?:跑下|跑一下|跑一遍|跑跑|跑(?!起来)).{0,24}"
        r"(?:测试|用例|pytest|pytes|全量|单测|回归)",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        r"\b(?:run|execute|re[- ]?run|rn|rnu)\b.{0,24}"
        r"\b(?:pytest|pytes|pyetst)\b",
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
]

_DOCUMENT_PATTERNS = [
    (
        r"(?:只|先|直接)?\s*(?P<action>改|更新|修订|补)"
        r"(?:一?下)?\s*(?:readme|docs?|documentation|文档|说明)|"
        r"(?:readme|docs?|documentation|文档|说明).{0,32}"
        r"(?P<action_tail>加个|添加|补|更新|修订).{0,32}"
        r"(?:quickstart|快速开始|用法|示例|步骤)?",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
    (
        r"(?P<action>改)(?:一?下)?(?:这|那|其中)?"
        r"(?:句|段)?(?:措辞|文案|表述|文字|句子)",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
    (
        (
            r"(?:\b(?:write|compose|create)\b.{0,48}\b"
            r"(?:poems?|stories?|lyrics?)\b|"
            r"(?:写|创作|来).{0,48}(?:短诗|诗歌|诗|故事|歌词|文案))"
        ),
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    # "document this function" is a common request but had no target noun
    # covering code symbols at all — only docs/comment/README/documentation.
    (
        r"\b(?:document|comment|write)\b.{0,20}\b(?:docs?|comments?|readme|documentation|functions?|classes?|methods?|code)\b",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"\b(?:update|updata|revise|edit)\b.{0,24}\b(?:docs?|readme|documentation|guides?)\b",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
    (
        r"(?:更新|修订|补(?:充|进|到|上)?).{0,80}"
        r"(?:readme|文档|说明|指南|手册|注释|docstring)",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:write|create|update)\b.{0,30}\b(?:docstrings?|"
        r"migration\s+guides?)\b",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"(?:编写|撰写).{0,60}"
        r"(?:readme|文档|说明|指南|注释|docstring)",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"(?:写一份|整理一份).{0,60}(?:规则|规范|清单|流程)",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"(?:写成|转成|转换成|写|编写|整理|生成|输出).{0,60}"
        r"(?:markdown|md|readme|adr|rfc|spec(?:ification)?|"
        r"文档|说明|报告|指南|架构决策记录)(?:\s*文件)?",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"(?:记录|整理).{0,80}(?:结论|发现|差异|步骤).{0,50}"
        r"(?:markdown|文档|报告|指南|手册|交接)?",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"(?:readme|docs?|documentation|指南|文档|手册).{0,64}"
        r"(?:update|updata|revise|edit|更新|修订|补(?:一?下|进去)?)",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
    (
        r"(?:结论|发现|结果).{0,64}(?:扔|放|塞|写|补)(?:进|到|入)"
        r".{0,24}(?:markdown|md|readme|文档|报告)",
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        r"(?:readme|docs?|文档|说明).{0,80}"
        r"(?:把|将)?.{0,24}(?:结论|发现|结果).{0,24}"
        r"(?:补|写(?!成|为)|放|塞)(?:进|到|入)?(?:去|来|里面)?|"
        r"(?:把|将)?(?:刚才|上述|前述)?.{0,12}(?:结论|发现|结果)"
        r".{0,32}(?:补|写(?!成|为)|放|塞)(?:进|到|入)?(?:去|来|里面)?"
        r".{0,24}(?:readme|docs?|文档|说明)?",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
]

_CONFIGURE_PATTERNS = [
    (
        r"(?P<action>升级|升一?下)\s*"
        r"(?:packages?|dependencies|dependency|libraries?|pytest|requests?|ruff|mypy)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:依赖|包|版本).{0,24}(?P<action>升级|升一?下)|"
        r"(?:把|将)\s*(?:requests?|pytest|ruff|mypy|django|flask|numpy|pandas)"
        r"\s*(?P<action_package>升级|升一?下)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:依赖|包|版本).{0,12}"
        r"(?:不要|不用|不必|无需|无须|别|甭|不)\s*"
        r"(?P<action>升级)|"
        r"(?P<action_positive>升级).{0,32}(?:依赖|包|版本)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    # Same problem as EXPLAIN: "install" / "init" / "set up" alone match a
    # huge range of everyday sentences ("set up a meeting", "install a new
    # habit"). Require a settings/environment/project-shaped noun nearby.
    (
        (
            r"\badd\b.{0,48}\b(?:yaml|json|toml|xml|csv)\s+parser\b"
            r".{0,48}\b(?:projects?|applications?|codebases?|dependencies?)\b|"
            r"\b(?:add|upgrade|downgrade|replace|remove)\b.{0,64}"
            r"\b(?:pinned\s+)?(?:dependencies?|packages?|libraries?|versions?)\b|"
            r"\b(?:upgrade|downgrade|pin)\b.{0,48}"
            r"\b(?:pytest|ruff|mypy|django|flask|requests?)\b.{0,24}"
            r"\bversion\b"
        ),
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:configure|set\s*up|install|initialize|init)\b.{0,60}"
        r"(?:setting|settings|environment|env|config|dependency|dependencies|"
        r"project|model|server|tool|package|repo|repository|workspace|"
        r"api key|credential)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:create|checkout|switch|merge|rebase)\b.{0,40}"
        r"\b(?:git\s+)?(?:branches?|commits?|tags?|merge|rebase)\b",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:配置|安装|初始化).{0,60}"
        r"(?:设置|环境|依赖|项目|模型|服务器|工具|包|仓库|工作区|密钥|"
        r"docker|容器|部署|流水线)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:创建|切换|合并|变基).{0,40}(?:git|分支|提交|标签)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:实施|自动|加入|配置|构建).{0,100}"
        r"(?:灰度|发布|部署|回滚|放量|上线|镜像|流水线)|"
        r"(?:灰度|发布|部署).{0,80}(?:回滚|放量|上线|自动化)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:拆成|拆分|整理成|各建|创建).{0,80}"
        r"(?:git|分支|提交|commit|可回滚记录)|"
        r"(?:整理).{0,50}(?:分支|提交)(?:历史)?",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:发版|发布|tag).{0,100}(?:自己|自动).{0,60}"
        r"(?:灰度|上线|回滚|回退)|"
        r"(?:串成|连成|接成).{0,80}(?:tag|发布|灰度).{0,80}"
        r"(?:回滚|回退|放量)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?:commit|提交).{0,80}(?:劈|拆|分).{0,32}(?:俩|两|多个|几个)|"
        r"(?:劈|拆|分).{0,32}(?:俩|两|多个|几个).{0,80}(?:commit|提交)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?:git|分支|提交|部署|deploy(?:ment)?).{0,48}"
        r"(?:不要|别|do\s+not|don['’]?t|no).{0,24}"
        r"(?:动|碰|改|建|部署|deploy)|"
        r"(?:不要|别).{0,24}(?:建|创建).{0,32}(?:git|分支)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"\b(?:no|without)\s+deploy(?:ment)?\b|"
        r"(?:不要|别|甭)\s*(?:部署|发布|上线)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
]

_MEMORY_PATTERNS = [
    (r"(?:remember|memorize|/memory|# remember)", IntentType.MEMORY, ActionType.CREATE),
    (
        r"(?:^|(?<=[,，。！？;；]))\s*"
        r"(?:(?:请(?:你)?|帮我|麻烦(?:你)?|我希望你|我想让你)\s*)?"
        r"(?:(?:不要|不用|无需|无须|不必|别|不)\s*)?"
        r"(?P<action>记住|记下)(?!来?的|了|过)",
        IntentType.MEMORY,
        ActionType.CREATE,
    ),
    (
        r"(?:\b(?:persistent|agent|project|user|long[- ]term)\s+memory\b|"
        r"\bmemory[-\s]+(?:retrieval|store|entry|lesson|profile|system)\b)",
        IntentType.MEMORY,
        ActionType.CREATE,
    ),
]

_SYSTEM_PATTERNS = [
    (
        r"^(?:/|!)(?:exit|quit|bye|clear|reset|help|settings|config|model|mode)",
        IntentType.SYSTEM,
        ActionType.EXECUTE,
    ),
]

_CHAT_PATTERNS = [
    (
        (
            r"^\s*(?:你(?:好|好呀|好啊)|您好|嗨|哈[喽啰]|"
            r"早上好|下午好|晚上好|hello|hi|hey|"
            r"good\s+(?:morning|afternoon|evening))"
            r"(?:\s*[!！,.，。])?\s*$"
        ),
        IntentType.CHAT,
        ActionType.READ,
    ),
]

_QUESTION_PATTERNS = [
    (
        r"(?:午饭|午餐|晚饭|晚餐|早餐|早饭)"
        r"[^。！？!?;；\n]{0,16}(?:去哪|在哪|哪里)(?:吃|解决)?",
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        r"(?P<action_usage>(?:我)?(?:只)?想知道)"
        r"[^,，。！？;；\n]{0,40}(?:skills?|技能)"
        r"[^,，。！？;；\n]{0,20}(?:用途|作用|做什么|干什么)|"
        r"(?P<action_missing>告诉我)"
        r"[^,，。！？;；\n]{0,24}(?:readme|文档|手册)"
        r"[^,，。！？;；\n]{0,16}(?:缺|少)"
        r"[^,，。！？;；\n]{0,16}(?:哪些|什么)"
        r"(?:章节|内容|部分)?",
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        r"(?P<action>(?<![输导])出|问)(?:我)?(?:两|几|\d+)(?:道|个)?(?:题|问题)",
        IntentType.QUESTION,
        ActionType.CREATE,
    ),
    (
        r"\b(?P<action>ask\s+me|quiz\s+me)\b.{0,48}"
        r"\b(?:interview\s+)?questions?\b.{0,80}"
        r"\b(?:minicode|code|architecture|agent|skill|routing)\b",
        IntentType.QUESTION,
        ActionType.CREATE,
    ),
    (
        r"(?:看|告诉我|说说)?[^,，。！？;；\n]{0,20}"
        r"(?:skill\s+router|技能路由|skill\s+routing)"
        r"[^,，。！？;；\n]{0,40}"
        r"(?P<action>选谁|选哪个|怎么选|会选(?:谁|哪个))",
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        (
            r"(?:(?:请|只)?(?:回答|告诉我|算(?:一)?下)\s*)?"
            r"[-+]?\d+(?:\.\d+)?\s*(?:加|减|乘|除以|[+\-*/×÷])\s*"
            r"[-+]?\d+(?:\.\d+)?\s*(?:等于|是|得)?\s*(?:多少|几)|"
            r"\b(?:what\s+is|calculate)\s+[-+]?\d+(?:\.\d+)?\s*"
            r"[+\-*/]\s*[-+]?\d+(?:\.\d+)?"
        ),
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        (
            r"(?:这个|该)?(?:名字|名称|命名).{0,20}"
            r"(?:是否|是不是|能否).{0,20}"
            r"(?:容易理解|好懂|清楚|明确|合适|恰当)|"
            r"(?:是否|是不是|能否).{0,20}"
            r"(?:容易理解|好懂|清楚|明确|合适|恰当).{0,20}"
            r"(?:名字|名称|命名)"
        ),
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        (
            r"(?:今天|明天|后天|昨天|现在|当前).{0,24}"
            r"(?:几月几日|星期几|周几|几点|日期|时间)|"
            r"\b(?:what(?:'s|\s+is)?|tell\s+me).{0,24}"
            r"(?:today(?:'s)?\s+date|day\s+of\s+the\s+week|time)\b"
        ),
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        (
            r"(?:\bhow\s+(?:do\s+(?:you|i)\s+)?translate\b|"
            r"(?:这个词|这个短语|这句话|[A-Za-z][\w-]{0,40}\s+这个词)"
            r".{0,20}(?:怎么|如何)翻译|"
            r"(?:把|将).{0,120}(?:翻成|翻译成)(?:英文|英语|中文|汉语))"
        ),
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        r"(?:\b[a-z][a-z0-9_-]{1,40}\b|[\u4e00-\u9fff]{1,20})"
        r"\s+(?:在[^。！？?\n]{0,32})?(?:是什么意思|啥意思|什么含义)|"
        r"\bwhat\s+does\s+[a-z][a-z0-9_-]{1,40}\s+mean\b",
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        (
            r"(?:朝|在|位于).{0,16}(?:哪个|什么|哪一)(?:个)?方向|"
            r"\b(?:which|what)\s+direction\b"
        ),
        IntentType.QUESTION,
        ActionType.READ,
    ),
    (
        (
            r"(?:附近|周边|哪(?:个|家)|哪个|哪些|"
            r"周[一二三四五六日天]|星期[一二三四五六日天]|"
            r"\b(?:nearby|which|what|sundays?|mondays?|tuesdays?|"
            r"wednesdays?|thursdays?|fridays?|saturdays?)\b)"
            r".{0,60}(?:营业|开门|营业时间|\b(?:open|opening\s+hours?)\b)"
        ),
        IntentType.QUESTION,
        ActionType.READ,
    ),
]

# Exact, bounded repairs for common technical typos and pinyin/code-switching.
# These deliberately require a nearby technical target or a multi-token phrase:
# short fragments such as ``cha`` and ``pin`` are too ambiguous to normalize
# globally and must never grant write/execute authority on their own.
_NOISY_MIXED_ACTION_PATTERNS = [
    (
        r"(?<![a-z0-9_])(?P<action>expln)(?![a-z0-9_]).{0,80}"
        r"(?:"
        r"(?<![a-z0-9_])(?:agent(?:_loop)?|tool|call|code|module|function|"
        r"parser|router|flow)(?![a-z0-9_])|"
        r"(?<![a-z0-9_])(?P<file>[\w/\\._-]+\.(?:py|js|ts|go|rs))"
        r")",
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        r"(?:挂|失败|红).{0,24}(?:顺手)?"
        r"(?P<action>修(?:咯|了|掉|下|一下)?)",
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        r"(?<![a-z0-9_])(?P<action>refacor)(?![a-z0-9_]).{0,48}"
        r"(?<![a-z0-9_])(?:parser|router|code|module|function|class)"
        r"(?![a-z0-9_])",
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        r"(?<![a-z0-9_])(?P<action>cha)(?![a-z0-9_]).{0,12}"
        r"(?<![a-z0-9_])woker\s+pool(?![a-z0-9_]).{0,32}"
        r"(?<![a-z0-9_])deallock(?![a-z0-9_])",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"(?:(?<![a-z0-9_])ba(?![a-z0-9_])|把).{0,40}"
        r"(?<![a-z0-9_])(?:pkg|package)(?![a-z0-9_]).{0,24}"
        r"(?P<action>(?<![a-z0-9_])gai\s*名)",
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        r"(?<![a-z0-9_])imports?(?![a-z0-9_]).{0,16}"
        r"(?P<action>(?<![a-z0-9_])bie\s*漏)",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
    (
        r"(?<![a-z0-9_])readme(?![a-z0-9_]).{0,24}"
        r"(?<![a-z0-9_])yongfa(?![a-z0-9_]).{0,16}"
        r"(?P<action>(?<![a-z0-9_])bu(?![a-z0-9_])(?:一?下)?)",
        IntentType.DOCUMENT,
        ActionType.UPDATE,
    ),
    (
        r"(?<![a-z0-9_])reqs(?![a-z0-9_]).{0,48}"
        r"(?<![a-z0-9_])(?:pytes|pytest)(?![a-z0-9_]).{0,24}"
        r"(?P<action>(?<![a-z0-9_])pin\s+sheng(?![a-z0-9_]))"
        r".{0,24}(?:新版本|version)",
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        r"(?<![a-z0-9_])(?P<action>profil)(?![a-z0-9_]).{0,40}"
        r"(?<![a-z0-9_])startup(?![a-z0-9_])",
        IntentType.DEBUG,
        ActionType.ANALYZE,
    ),
    (
        r"按\s*(?P<action>(?<![a-z0-9_])TDD(?![a-z0-9_]))",
        IntentType.TEST,
        ActionType.CREATE,
    ),
    (
        r"(?P<action>整个|实现|开发).{0,24}"
        r"(?<![a-z0-9_])(?:parser|router|feature|feat)(?![a-z0-9_])",
        IntentType.CODE,
        ActionType.CREATE,
    ),
    (
        r"(?<![a-z0-9_])git\s+diff(?![a-z0-9_]).{0,24}"
        r"(?P<action>瞅(?:一)?眼|看(?:一)?下|review|check)",
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        r"(?<![a-z0-9_])(?P<action>comit)(?![a-z0-9_])"
        r"(?!\s*(?:只是|是(?:一个|日志|字符串|单词|字段)|这个词|means?\b))",
        IntentType.CODE,
        ActionType.UPDATE,
    ),
]

_ALL_PATTERNS = (
    _SYSTEM_PATTERNS
    + _CHAT_PATTERNS
    + _QUESTION_PATTERNS
    + _DELETE_PATTERNS
    + _MOVE_PATTERNS
    + _RENAME_PATTERNS
    + _COMPARE_PATTERNS
    + _NOISY_MIXED_ACTION_PATTERNS
    + _PRECISE_DEBUG_PATTERNS
    + _REVIEW_PATTERNS
    + _MEMORY_PATTERNS
    + _CODE_PATTERNS
    + _DEBUG_PATTERNS
    + _REFACTOR_PATTERNS
    + _EXPLAIN_PATTERNS
    + _SEARCH_PATTERNS
    + _TEST_PATTERNS
    + _DOCUMENT_PATTERNS
    + _CONFIGURE_PATTERNS
)

# Raw tokens cap below the total so cross-language aliases always fit.
_MAX_RAW_KEYWORDS = 14
_MAX_KEYWORDS = 20

_CHINESE_KEYWORD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("评价", ("review",)),
    ("评估", ("review",)),
    ("点评", ("review",)),
    ("审视", ("review",)),
    ("审查", ("audit", "review")),
    ("检查", ("inspect", "review")),
    ("审核", ("audit", "review")),
    ("审计", ("audit", "review")),
    ("持久化", ("persistent",)),
    ("长期记忆", ("persistent memory", "agent memory")),
    ("记忆", ("memory",)),
    ("内存", ("memory",)),
    ("技能", ("skill",)),
    ("路由", ("routing",)),
    ("自进化", ("evolution",)),
    ("进化", ("evolution",)),
    ("调试", ("debug",)),
    ("测试", ("test",)),
    ("用例", ("test", "test case")),
    ("复现", ("regression", "test")),
    ("架构", ("architecture",)),
    ("项目", ("project",)),
    ("上下文", ("context",)),
    ("压缩", ("compaction",)),
    ("摘要", ("summary",)),
    ("多智能体", ("agent-orchestration", "multi-agent", "agent")),
    ("子智能体", ("agent-orchestration", "sub-agent", "agent")),
    ("多代理", ("agent",)),
    ("子代理", ("agent-orchestration", "sub-agent", "agent")),
    ("委派", ("delegation",)),
    ("工作单元", ("agent-orchestration", "multi-agent", "agent", "delegation")),
    ("任务分配", ("delegation",)),
    ("分工", ("delegation",)),
    ("汇总", ("aggregation",)),
    ("重试", ("retry",)),
    ("并行", ("parallel",)),
    ("登录", ("authentication",)),
    ("串号", ("authentication", "permission", "security")),
    ("鉴权", ("authentication",)),
    ("权限", ("permission",)),
    ("越权", ("permission", "security")),
    ("绕过", ("bypass", "permission", "security")),
    ("管理员", ("permission",)),
    ("密钥", ("secret",)),
    ("凭据", ("credential", "secret")),
    ("注入", ("injection", "security")),
    ("泄漏", ("leak",)),
    ("数据库", ("database",)),
    ("查询", ("query",)),
    ("配置", ("config", "configuration")),
    ("依赖", ("dependency", "package")),
    ("版本", ("version",)),
    ("断言", ("assertion", "test")),
    ("红了", ("failure", "test")),
    ("挂了", ("failure",)),
    ("抽风", ("failure",)),
    ("定义", ("definition", "reference")),
    ("引用", ("reference",)),
    ("调用方", ("caller", "callers", "reference")),
    ("索引", ("index",)),
    ("部署", ("deploy",)),
    ("发布", ("deploy", "release")),
    ("发版", ("deploy", "release")),
    ("灰度", ("deploy", "rollout")),
    ("回滚", ("rollback",)),
    ("回归", ("regression", "test")),
    ("回退", ("rollback",)),
    ("提交历史", ("git-workflow", "git", "commit", "history")),
    ("自动灰度发布", ("deployment-automation", "deploy", "release", "rollout")),
    ("发布自动化", ("deployment-automation", "deploy", "release")),
    ("自动化部署", ("deployment-automation", "deploy", "pipeline")),
    ("部署自动化", ("deployment-automation", "deploy", "pipeline")),
    ("部署流水线", ("deployment-automation", "deploy", "pipeline")),
    ("可回滚记录", ("commit", "history")),
    ("开发线", ("git-workflow", "branch")),
    ("放量", ("rollout",)),
    ("上线", ("deploy",)),
    ("镜像", ("image", "container")),
    ("容器", ("container",)),
    ("流水线", ("pipeline",)),
    ("文档", ("documentation",)),
    ("指南", ("guide",)),
    ("手册", ("manual", "documentation")),
    ("前端", ("frontend",)),
    ("组件", ("component",)),
    ("布局", ("layout",)),
    ("移动端", ("mobile", "frontend")),
    ("手机", ("mobile", "frontend")),
    ("响应式", ("responsive", "frontend")),
    ("溢出", ("overflow", "layout")),
    ("调用链", ("call flow",)),
    ("调用路径", ("call path",)),
    ("执行路径", ("execution path",)),
    ("状态流", ("state flow",)),
    ("数据流", ("data flow",)),
    ("组件层级", ("component hierarchy",)),
    ("召回", ("retrieval",)),
    ("来源链", ("provenance",)),
    ("偏好", ("preference",)),
    ("性能", ("performance",)),
    ("卡顿", ("slow", "performance")),
    ("慢", ("slow", "performance")),
    ("启动", ("startup",)),
    ("耗时", ("performance", "latency")),
    ("磨磨唧唧", ("performance", "latency", "slow")),
    ("烧哪", ("bottleneck", "profile", "performance")),
    ("热点", ("profile", "performance")),
    ("运行时", ("runtime", "performance")),
    ("锁等待", ("profile", "latency")),
    ("锁", ("lock", "concurrency")),
    ("偶发", ("intermittent", "flaky")),
    ("挂住", ("deadlock", "concurrency")),
    ("重命名", ("rename",)),
    ("改名", ("rename",)),
    ("行为保持", ("behavior preserving",)),
    ("挪", ("move",)),
    ("优缺点", ("design review", "tradeoffs")),
    ("取舍", ("design review", "tradeoffs")),
    ("全表扫描", ("query", "database", "sql", "performance")),
    ("扫全表", ("query", "database", "sql", "performance")),
    ("扫整表", ("query", "database", "sql", "performance")),
    ("延迟", ("latency",)),
    ("分支", ("branch",)),
    ("提交", ("commit",)),
    ("改动", ("diff", "change")),
)

_NOISY_KEYWORD_ALIASES: tuple[
    tuple[re.Pattern[str], tuple[str, ...]], ...
] = (
    (
        re.compile(
            r"(?<![a-z0-9_])git(?:\s+(?:branch|commit|history|release|workflow))?"
            r"(?![a-z0-9_])|"
            r"(?<![a-z0-9_])commit\s+history(?![a-z0-9_])",
            re.IGNORECASE,
        ),
        ("git-workflow", "git"),
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:multi[- ]agent|sub[- ]?agent|worker[- ]agent|"
            r"agent\s+task)\s+(?:worker\s+)?"
            r"(?:(?:task\s+|任务\s*)?(?:delegation|dispatch|split|scheduling|"
            r"scheduler|委派|拆分|调度)\s+)?"
            r"(?:orchestration|scheduling|workflow|system|pipeline|tasks?|"
            r"工作流|系统|流程)"
            r"(?![a-z0-9_])|"
            r"(?:并行代理(?:任务)?(?:编排|委派)|(?:多代理|子?代理|子?智能体)"
            r"[^。！？!?;；\n]{0,10}(?:任务)?(?:编排|调度|拆分|委派))",
            re.IGNORECASE,
        ),
        ("agent-orchestration", "multi-agent", "agent"),
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:deployment\s+automation(?:\s+workflow)?|"
            r"automated\s+(?:canary\s+)?deployment(?:\s+pipeline)?|"
            r"canary\s+(?:deployment|release)\s+(?:automation|pipeline)|"
            r"production\s+(?:canary\s+release\s+workflow|"
            r"rollout\s+automation(?:\s+pipeline)?)|"
            r"kubernetes\s+(?:(?:deployment|灰度)\s+)?rollout\s+automation|"
            r"repository\s+(?:branch|分支)\s+release\s+automation)"
            r"(?![a-z0-9_])|"
            r"(?:自动化生产部署流水线|发布分支自动化流程)",
            re.IGNORECASE,
        ),
        ("deployment-automation", "deploy", "release", "pipeline"),
    ),
    (
        re.compile(
            r"(?:(?<![输导])出|问)(?:我)?(?:两|几|\d+)(?:道|个)?(?:题|问题)"
        ),
        ("quiz", "question"),
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:refs?|references?)(?![a-z0-9_])",
            re.IGNORECASE,
        ),
        ("reference", "references"),
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:requests?|pytest|ruff|mypy|django|flask|"
            r"numpy|pandas)(?![a-z0-9_])\s*(?:升级|升一?下)",
            re.IGNORECASE,
        ),
        ("dependency", "package", "upgrade"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])pytes(?![a-z0-9_])", re.IGNORECASE),
        ("pytest", "test"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])refacor(?![a-z0-9_])", re.IGNORECASE),
        ("refactor",),
    ),
    (
        re.compile(r"(?<![a-z0-9_])expln(?![a-z0-9_])", re.IGNORECASE),
        ("explain",),
    ),
    (
        re.compile(r"(?<![a-z0-9_])explian(?![a-z0-9_])", re.IGNORECASE),
        ("explain",),
    ),
    (
        re.compile(r"(?<![a-z0-9_])updata(?![a-z0-9_])", re.IGNORECASE),
        ("update",),
    ),
    (
        re.compile(r"(?<![a-z0-9_])woker(?![a-z0-9_])", re.IGNORECASE),
        ("worker", "concurrency"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])deallock(?![a-z0-9_])", re.IGNORECASE),
        ("deadlock", "concurrency"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])injction(?![a-z0-9_])", re.IGNORECASE),
        ("injection", "security"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])credetial(?![a-z0-9_])", re.IGNORECASE),
        ("credential", "secret"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])pkg(?![a-z0-9_])", re.IGNORECASE),
        ("package",),
    ),
    (
        re.compile(r"(?<![a-z0-9_])yongfa(?![a-z0-9_])", re.IGNORECASE),
        ("usage", "documentation"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])reqs(?![a-z0-9_])", re.IGNORECASE),
        ("requirements", "dependency"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])profil(?![a-z0-9_])", re.IGNORECASE),
        ("profile", "performance"),
    ),
    (
        re.compile(r"(?<![a-z0-9_])comit(?![a-z0-9_])", re.IGNORECASE),
        ("commit",),
    ),
    (
        re.compile(r"\bcapablit(?:y|es)|capabilites\b", re.IGNORECASE),
        ("capability",),
    ),
    (
        re.compile(r"\bfull[- ]scann\w*\b", re.IGNORECASE),
        ("query", "database", "sql", "performance"),
    ),
    (
        re.compile(r"\bfull[- ]table\s+scan\b", re.IGNORECASE),
        ("query", "database", "sql", "performance"),
    ),
    (re.compile(r"\bprivilege\s+bypass\b", re.IGNORECASE), ("permission", "security")),
    (
        re.compile(
            r"\b(?:audit|review|inspect|check)\b.{0,32}"
            r"\bauth(?:entication)?(?:\.py)?\b",
            re.IGNORECASE,
        ),
        ("authentication", "permission", "security"),
    ),
    (
        re.compile(
            r"\b(?:auth(?:entication)?|permission|privilege)\b.{0,24}"
            r"\bbypass\b",
            re.IGNORECASE,
        ),
        ("authentication", "permission", "security"),
    ),
    (
        re.compile(
            r"\b(?:optimi[sz](?:e|ing|ation)|tun(?:e|ing))\b.{0,48}"
            r"\b(?:sql|database|quer(?:y|ies)|index)\b|"
            r"(?:优化|调优).{0,32}(?:数据库|查询|索引)",
            re.IGNORECASE,
        ),
        ("query", "database", "sql", "performance"),
    ),
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
    operations: tuple[IntentOperation, ...] = ()
    primary_operation_index: int | None = None
    evidence: tuple[IntentEvidence, ...] = ()
    decision_reason: str = ""
    diagnostics: IntentDiagnostics = field(default_factory=IntentDiagnostics)
    semantic_query: str = ""
    context_used: bool = False
    # Bounded user-turn text used only to preserve explicit routing denials.
    # It is deliberately omitted from serialized projections: context is
    # ephemeral routing input, not part of the legacy intent contract.
    routing_directive_context: tuple[str, ...] = ()

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
        return any(
            operation.intent_type
            in {
                IntentType.CODE,
                IntentType.DEBUG,
                IntentType.REFACTOR,
                IntentType.REVIEW,
                IntentType.TEST,
            }
            for operation in self.affirmed_operations
        ) or (
            not self.operations
            and self.intent_type
            in {
                IntentType.CODE,
                IntentType.DEBUG,
                IntentType.REFACTOR,
                IntentType.REVIEW,
                IntentType.TEST,
            }
        )

    def is_read_only(self) -> bool:
        operations = self.affirmed_operations
        if not operations:
            if self.operations:
                # A request containing only prohibitions authorizes no write.
                return True
            return self.action_type in {
                ActionType.READ,
                ActionType.ANALYZE,
                ActionType.COMPARE,
            }
        return all(
            operation.action_type
            in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
            for operation in operations
        )

    @property
    def primary_operation(self) -> IntentOperation | None:
        if (
            self.primary_operation_index is not None
            and 0 <= self.primary_operation_index < len(self.operations)
        ):
            operation = self.operations[self.primary_operation_index]
            if operation.polarity is IntentPolarity.AFFIRMED:
                return operation
        if not self.operations and self.intent_type is not IntentType.UNKNOWN:
            return IntentOperation(
                intent_type=self.intent_type,
                action_type=self.action_type,
                polarity=IntentPolarity.AFFIRMED,
                confidence=self.confidence,
                clause=self.raw_input,
                entities={key: tuple(values) for key, values in self.entities.items()},
                keywords=tuple(self.keywords),
                evidence_codes=("compat:legacy-scalar",),
            )
        return None

    @property
    def affirmed_operations(self) -> tuple[IntentOperation, ...]:
        return tuple(
            operation
            for operation in self.operations
            if operation.polarity is IntentPolarity.AFFIRMED
        )

    @property
    def negated_operations(self) -> tuple[IntentOperation, ...]:
        return tuple(
            operation
            for operation in self.operations
            if operation.polarity is IntentPolarity.NEGATED
        )

    def to_detailed_dict(self) -> dict[str, Any]:
        """Return the V2 projection without changing the legacy schema."""
        detailed = self.to_dict()
        detailed.update(
            {
                "operations": [
                    {
                        "intent_type": operation.intent_type.value,
                        "action_type": operation.action_type.value,
                        "polarity": operation.polarity.value,
                        "confidence": operation.confidence,
                        "clause": operation.clause,
                        "span": list(operation.span) if operation.span else None,
                        "entities": {
                            key: list(values)
                            for key, values in operation.entities.items()
                        },
                        "keywords": list(operation.keywords),
                        "evidence_codes": list(operation.evidence_codes),
                    }
                    for operation in self.operations
                ],
                "primary_operation_index": self.primary_operation_index,
                "evidence": [
                    {
                        "code": item.code,
                        "source": item.source.value,
                        "reason": item.reason,
                        "strength": item.strength,
                        "span": list(item.span) if item.span else None,
                        "turn_offset": item.turn_offset,
                    }
                    for item in self.evidence
                ],
                "decision_reason": self.decision_reason,
                "diagnostics": {
                    "semantic_status": self.diagnostics.semantic_status,
                    "context_turns_used": self.diagnostics.context_turns_used,
                    "warnings": list(self.diagnostics.warnings),
                },
                "semantic_query": self.semantic_query,
                "context_used": self.context_used,
            }
        )
        return detailed


@dataclass(frozen=True, slots=True)
class IntentTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class IntentContext:
    """Explicit, bounded per-turn context; the parser stores no session state."""

    recent_turns: tuple[IntentTurn, ...] = ()
    previous_intent: ParsedIntent | None = None
    active_task: str = ""

    @classmethod
    def from_messages(
        cls,
        messages: Sequence[Mapping[str, Any]],
        *,
        previous_intent: ParsedIntent | None = None,
        active_task: str = "",
    ) -> IntentContext:
        selected: list[IntentTurn] = []
        remaining_chars = 4000
        for message in reversed(tuple(messages)):
            if len(selected) >= 6 or remaining_chars <= 0:
                break
            if not isinstance(message, Mapping):
                continue
            role = str(message.get("role", "")).strip().lower()
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            normalized = content.strip()
            if not normalized:
                continue
            bounded = normalized[: min(1000, remaining_chars)]
            remaining_chars -= len(bounded)
            selected.append(IntentTurn(role=role, content=bounded))
        selected.reverse()
        return cls(
            recent_turns=tuple(selected),
            previous_intent=previous_intent,
            active_task=str(active_task or "").strip()[:1000],
        )


@dataclass(frozen=True, slots=True)
class SemanticIntentRequest:
    text: str
    context: IntentContext
    deterministic_operations: tuple[IntentOperation, ...]


@dataclass(frozen=True, slots=True)
class SemanticIntentProposal:
    intent_type: IntentType
    action_type: ActionType
    polarity: IntentPolarity
    confidence: float
    clause: str
    span: tuple[int, int] | None = None
    reason_code: str = "semantic:proposal"
    reason: str = "semantic intent proposal"


class IntentSemanticAdapter(Protocol):
    def infer(
        self,
        request: SemanticIntentRequest,
        *,
        call_context: Mapping[str, Any] | None = None,
    ) -> tuple[SemanticIntentProposal, ...]: ...


_SEMANTIC_DIAGNOSTIC_STATUSES = frozenset(
    {
        "abstained",
        "accepted",
        "budget_exhausted",
        "control_unavailable",
        "disabled",
        "invalid_response",
        "low_confidence",
        "network",
        "provider_error",
        "timeout",
        "tool_call_rejected",
        "used",
    }
)

_QUOTED_INTENT_EXAMPLE_RE = re.compile(
    r'"[^"\r\n]*"|“[^”\r\n]*”|‘[^’\r\n]*’|'
    r"「[^」\r\n]*」|『[^』\r\n]*』|"
    r"(?<!\w)'[^'\r\n]*'(?!\w)"
)
_INLINE_CODE_INTENT_EXAMPLE_RE = re.compile(
    r"(?<!`)(?P<delimiter>`{1,2})(?!`)[^\r\n]*?"
    r"(?P=delimiter)(?!`)"
)
_TRIPLE_CODE_INTENT_EXAMPLE_RE = re.compile(
    r"(?P<delimiter>`{3,}|~{3,})[\s\S]*?(?P=delimiter)"
)
_MARKDOWN_CODE_FENCE_OPEN_RE = re.compile(
    r"(?m)^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)"
)
_DECLARED_NON_COMMAND_PREFIX_RE = re.compile(
    r"(?:^|[。！？!?；;\n])\s*(?:"
    r"(?:下面|以下)(?:是|为)\s*(?:一段|一个)?\s*"
    r"(?:日志|示例|文本|文字|内容)\s*[,，]\s*(?:这|它)?\s*"
    r"(?:不是|并非)\s*(?:命令|指令|请求|执行请求)|"
    r"(?:下面|以下|下列|这段|这句)(?:内容|文字|文本|日志|示例)?\s*"
    r"(?:不是|并非)\s*(?:命令|指令|请求|执行请求)|"
    r"(?:这|它)?\s*(?:不是|并非)\s*(?:命令|指令|请求|执行请求)|"
    r"(?:(?:the\s+following|the\s+text\s+below|below|this(?:\s+"
    r"(?:text|sentence|example|log))?)\s+)?"
    r"(?:is\s+not|isn['’]?t|isnt|not)\s+(?:an?\s+)?"
    r"(?:command|instruction|request)"
    r")\s*[:：]\s*",
    re.IGNORECASE,
)
_DECLARED_NON_COMMAND_END_RE = re.compile(
    r"[,，]\s*(?=(?:只|仅)\s*(?:解释|说明|分析)(?:一?下)?\s*"
    r"(?:它|这(?:条|段|句|个)?|该(?:命令|内容)?|上面)|"
    r"(?:only|just)\s+(?:explain|describe)\b)|"
    r"[。！？!?；;\n]|\.(?=\s|$)",
    re.IGNORECASE,
)
_REPORTED_INSTRUCTION_LITERAL_RE = re.compile(
    r"(?:^|[。！？!?；;\n])\s*(?:"
    r"(?:例子|示例|案例|文章|博客|帖子|文档|说明|日志)(?:里|中)?\s*"
    r"(?:写着|写道|写的是|说|说道|提到)|"
    r"(?:the\s+)?(?:example|article|blog|post|document|log)\s+"
    r"(?:says?|reads?|states?|mentions?|is(?=\s*[\"'“‘`]))"
    r")\s*[:：]?\s*[^,，。！？!?；;\n]+",
    re.IGNORECASE,
)
_REPORTED_OPERATION_EN_SOURCE_PATTERN = (
    r"(?:(?:in\s+)?(?:the|this|that|a|an)\s+)?"
    r"(?:(?:quoted|copied|release|deployment|incident|post[- ]incident|audit|"
    r"diagnostic|console|sidebar|log|sample|support)\s+){0,3}"
    r"(?:(?:slack|operator|customer)\s+)?"
    r"(?:memo|notes?|reports?|timeline|transcript|excerpt|annotation|records?|"
    r"summary|output|message|snippet|entry|evidence|postmortem|ticket|"
    r"table|attachment|comment)"
    r"(?:\s+(?:snippet|record|entry|message|summary|output))?"
)
_REPORTED_OPERATION_ZH_SOURCE_PATTERN = (
    r"(?:(?:故障|事故|审计|控制台|诊断|日志|工单|引用|发布|复盘|"
    r"旁边的|旁人的?)\s*){0,3}"
    r"(?:复盘|记录|摘要|摘录|转录|批注|旁注|证据|消息|句子|输出|报告|"
    r"备忘录|日志|表格|附件|说明|评论|工单|标题)"
    r"(?:里|中|里的|中的)?(?:那一栏|那句话|句子|示例命令|示例)?"
)
_REPORTED_OPERATION_SOURCE_PREFIX_RE = re.compile(
    r"(?:^|(?<=[。！？!?；;\n]))\s*(?:这里|这儿|以下|下面)?\s*"
    r"(?:只是|仅仅是|仅是)?\s*(?:粘贴|复制)(?:的)?\s*"
    r"(?:日志|文本|内容|消息)\s*[:：]\s*|"
    rf"(?:^|(?<=[。！？!?；;\n]))\s*"
    rf"{_REPORTED_OPERATION_EN_SOURCE_PATTERN}\s*"
    r"(?:,\s*(?:the\s+)?(?:operator|author|agent|user)\s+)?"
    r"(?:states?|reads?|says?|contains?|displays?|quotes?|mentions?|"
    r"records?|wrote|writes?|reported?)"
    r"\s*[:：]?\s*|"
    rf"(?:^|(?<=[。！？!?；;\n]))\s*"
    rf"{_REPORTED_OPERATION_EN_SOURCE_PATTERN}\s*[:：]\s*|"
    rf"(?:^|(?<=[。！？!?；;\n]))\s*"
    rf"{_REPORTED_OPERATION_ZH_SOURCE_PATTERN}\s*"
    r"(?:写的是|粘贴的是|写着|写道|写|说|转述|内容为|内容是|"
    r"内容(?=\s*[:：])|"
    r"原文(?:是|为)?|"
    r"显示|提到|记录(?:着)?|是|为)\s*[:：]?\s*|"
    r"(?:^|(?<=[。！？!?；;\n]))\s*(?:同事|朋友|用户|作者|他|她)"
    r"\s*(?:留言|说|写道)\s*[:：]?\s*",
    re.IGNORECASE,
)
_REPORTED_OPERATION_OUTER_BOUNDARY_RE = re.compile(
    r"[;；]\s*(?=(?:ignore\s+that|disregard(?:\s+it)?|but\b|"
    r"leave\s+that|actually\b|instead\b|separately\b|please\b|now\b|"
    r"your\s+task\b|what\s+i\s+(?:want|need)\b|"
    r"实际|现在|请|另外|但请|本次|这次|你只需|你只要|"
    r"别照做|不要照做|真正))|"
    r"[,，]\s*(?=(?:but\b|while\b|actually\b|instead\b|separately\b|"
    r"your\s+task\b|what\s+i\s+(?:want|need)\b|实际|现在|但请|"
    r"本次|这次|另外|别照做|不要照做))|"
    r"\.(?=\s+(?:Do\s+not|Don['’]?t|Separately|Actually|Instead|Please|Now|"
    r"Your\s+task|What\s+I\s+(?:want|need))\b)|"
    r"[。！？!?](?=\s*(?:实际|现在|请|另外|但请|本次|这次|别照做|"
    r"不要照做|真正|你只需|你只要))",
    re.IGNORECASE,
)


def _reported_operation_literal_spans(
    text: str,
) -> tuple[tuple[int, int], ...]:
    """Return attributed action text that cannot authorize an operation.

    The source must actively *report* its payload (``memo states ...``), so
    instructions that merely use a source (``from the memo, update ...``)
    remain actionable.  An explicit outer-request cue ends only that quoted
    payload and leaves the caller's real command visible to the parser.
    """

    spans: list[tuple[int, int]] = []
    for prefix in _REPORTED_OPERATION_SOURCE_PREFIX_RE.finditer(text):
        payload = text[prefix.end() :].lstrip()
        if payload.startswith(('"', "'", "“", "‘", "「", "『", "`", "~")):
            # Quote/code masking already provides an exact boundary.  Let it
            # handle the payload so an outer continuation after the closing
            # delimiter (``...”; api.py, continue check``) stays visible.
            continue
        boundary = _REPORTED_OPERATION_OUTER_BOUNDARY_RE.search(
            text,
            prefix.end(),
        )
        end = boundary.start() if boundary is not None else len(text)
        if prefix.start() < end:
            spans.append((prefix.start(), end))
    return tuple(spans)
_REPORTED_SKILL_INVOCATION_LITERAL_RE = re.compile(
    r"(?:^|[。！？!?；;\n])\s*(?:"
    r"(?:故障报告|报告|故障单|工单|审查意见|评审意见|会议纪要|"
    r"引用(?:文字|内容)|引文)(?:里|中)?\s*"
    r"(?:写着|写道|写的是|说|说道|提到|记录|建议|要求|是)|"
    r"(?:issue|ticket|report|review(?:er)?\s+comment|minutes)\s*"
    r"(?:里|中)?\s*(?:says?|reads?|states?|mentions?|suggests?|"
    r"requests?|写着|提到|建议|要求))\s*[:：]?\s*"
    r"(?:(?:请\s*)?(?:使用|调用|加载|应用)|"
    r"(?:please\s+)?(?:use|load|invoke|apply))\s*"
    r"(?:the\s+)?(?:skill\s+)?"
    r"[a-z0-9][a-z0-9._/:-]*[-_/:.][a-z0-9._/:-]*"
    r"(?:\s*(?:skill|技能))?",
    re.IGNORECASE,
)


def _declared_non_command_literal_spans(
    text: str,
) -> tuple[tuple[int, int], ...]:
    """Return unquoted example/log bodies explicitly denied as commands."""

    spans: list[tuple[int, int]] = []
    for prefix in _DECLARED_NON_COMMAND_PREFIX_RE.finditer(text):
        start = prefix.end()
        ending = _DECLARED_NON_COMMAND_END_RE.search(text, start)
        end = ending.start() if ending is not None else len(text)
        if start < end:
            spans.append((start, end))
    return tuple(spans)


def _protected_intent_literal_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return quoted/code/example spans that cannot grant authority.

    Spans are merged so nested quote syntax inside inline or fenced code is
    treated as one inert source literal.  Newlines remain available to the
    clause splitter, while all command words and connectors inside a span can
    be masked without changing offsets.
    """

    spans: list[tuple[int, int]] = []
    cursor = 0
    while opening := _MARKDOWN_CODE_FENCE_OPEN_RE.search(text, cursor):
        delimiter = opening.group("fence")
        closing_re = re.compile(
            rf"(?m)^[ \t]{{0,3}}{re.escape(delimiter[0])}"
            rf"{{{len(delimiter)},}}[ \t]*(?:\r?\n|$)"
        )
        closing = closing_re.search(text, opening.end())
        end = closing.end() if closing is not None else len(text)
        spans.append((opening.start(), end))
        cursor = end

    spans.extend(
        match.span() for match in _TRIPLE_CODE_INTENT_EXAMPLE_RE.finditer(text)
    )
    spans.extend(
        match.span() for match in _QUOTED_INTENT_EXAMPLE_RE.finditer(text)
    )
    spans.extend(
        match.span() for match in _INLINE_CODE_INTENT_EXAMPLE_RE.finditer(text)
    )
    spans.extend(attributed_literal_statement_spans(text, tuple(spans)))
    spans.extend(_declared_non_command_literal_spans(text))
    spans.extend(_reported_operation_literal_spans(text))
    spans.extend(
        match.span() for match in _REPORTED_INSTRUCTION_LITERAL_RE.finditer(text)
    )
    spans.extend(
        match.span()
        for match in _REPORTED_SKILL_INVOCATION_LITERAL_RE.finditer(text)
    )
    if not spans:
        return ()

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _mask_protected_intent_literals(text: str) -> str:
    """Mask quoted prose and Markdown code without changing source offsets."""

    visible_start, visible_end = _non_whitespace_bounds(text)
    if visible_start == visible_end:
        return text
    if visible_start or visible_end != len(text):
        return (
            text[:visible_start]
            + _mask_protected_intent_literals(text[visible_start:visible_end])
            + text[visible_end:]
        )
    characters = list(text)
    for start, end in _protected_intent_literal_spans(text):
        for index in range(start, end):
            if characters[index] not in "\r\n":
                characters[index] = " "
    return "".join(characters)


def _mask_quoted_intent_examples(text: str) -> str:
    """Compatibility name for the expanded protected-literal mask."""

    return _mask_protected_intent_literals(text)


def _non_whitespace_bounds(text: str) -> tuple[int, int]:
    """Return the smallest source-aligned window containing visible text."""

    start = 0
    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


_STRUCTURED_SKILL_IDENTIFIER = r"[a-z0-9][a-z0-9._/:-]*[-_/:.][a-z0-9._/:-]*"
_EXPLICIT_SKILL_IDENTIFIER_PATTERNS = (
    re.compile(
        rf"\$\s*`?(?P<identifier>{_STRUCTURED_SKILL_IDENTIFIER})`?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:(?:do\s+not|don't|never)\s+)?"
        rf"(?:use|load|invoke|apply|follow|select|choose|include|switch\s+to)"
        rf"\s+(?:the\s+)?(?:skill\s+(?:named\s+|called\s+)?)?`?"
        rf"(?P<identifier>{_STRUCTURED_SKILL_IDENTIFIER})`?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:(?:不要|不准|禁止|请勿|勿)\s*"
        rf"(?:使用|调用|加载|应用|选择)?|"
        rf"(?:使用|调用|加载|应用|按照|选择|选用|改用|改为))\s*`?"
        rf"(?P<identifier>{_STRUCTURED_SKILL_IDENTIFIER})`?"
        rf"(?:\s*(?:skill|技能))?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<identifier>{_STRUCTURED_SKILL_IDENTIFIER})\s*"
        rf"(?:就)?(?:用|使用)\s*(?:这个|该)\s*(?:skill|技能)",
        re.IGNORECASE,
    ),
)

_SOURCE_FILE_IDENTIFIER_RE = re.compile(
    r"\.(?:c|cc|cfg|cpp|cs|css|go|h|hpp|html|ini|java|js|jsx|json|kt|"
    r"kts|lock|md|php|ps1|py|pyi|rb|rs|rst|scala|scss|sh|sql|svelte|"
    r"swift|toml|ts|tsx|txt|vue|xml|ya?ml|zsh)(?::\d+(?::\d+)?)?$",
    re.IGNORECASE,
)


def _mask_explicit_skill_identifiers(text: str) -> str:
    """Hide authority-bearing catalog IDs from task taxonomy matching."""
    characters = list(text)
    for pattern in _EXPLICIT_SKILL_IDENTIFIER_PATTERNS:
        for match in pattern.finditer(text):
            identifier = match.group("identifier")
            full_reference = match.group(0)
            if (
                _SOURCE_FILE_IDENTIFIER_RE.search(identifier)
                and "$" not in full_reference
                and re.search(r"\bskills?\b|技能", full_reference, re.IGNORECASE)
                is None
            ):
                # ``switch to new.py`` and ``use auth.py`` select source
                # artifacts, not Skills with dotted identifiers.  A sigil or
                # explicit Skill label remains authoritative for unusually
                # named catalog entries.
                continue
            start, end = match.span("identifier")
            characters[start:end] = " " * (end - start)
    return "".join(characters)


_INTENT_REPLACEMENT_PREFIX_RE = re.compile(
    r"(?:^|[.!?;,，。！？；—])\s*(?:actually|no)\s*[,，:]?\s*"
    r"(?=(?:please\s+)?(?:use|load|invoke|apply|follow|select|choose|"
    r"include|switch\s+to)\b)|"
    r"(?:^|[.!?;,，。！？；—])\s*(?:nah\s+)?scratch\s+that\s*"
    r"(?:[-—,:，]\s*)?(?=(?:please\s+)?(?:use|load|invoke|apply|follow|"
    r"select|choose|include|switch\s+to|review|audit|inspect|check|fix|"
    r"repair|explain|search|run|write|update|refactor|optimize)\b)|"
    r"(?:^|[.!?;,，。！？；—])\s*(?:不对|不是这样|改主意了)\s*[,，:]?\s*"
    r"(?=(?:请\s*)?(?:改用|改为|使用|调用|加载|选择|选用))",
    re.IGNORECASE,
)

_GENERAL_INTENT_REPLACEMENT_PREFIX_RE = re.compile(
    r"(?:^|[.!?;,，。！？；—…])\s*(?:哦不|不对|改主意了)\s*[,，:]?\s*"
    r"(?=(?:还是\s*)?(?:请|先|只|别|不要|不用|不必|无需|无须)?\s*"
    r"(?:用|使用|调用|审|审查|审核|检查|看|查|解释|说明|修|改|跑|运行|"
    r"执行|写|补|更新|重构|优化))|"
    r"(?:^|[.!?;,，。！？；—])\s*不\s*[,，:]\s*"
    r"(?=(?:先\s*)?(?:(?:别|不要|不用|不必|无需|无须)\s*"
    r"(?:跑|运行|执行|修|改|写|删|审|查|看)|"
    r"(?:审|审查|审核|审计|看|检查|review|audit|inspect|check)))",
    re.IGNORECASE,
)

_CORRECTION_TOPIC_REUSE_RE = re.compile(
    r"(?:就是|还是)\s*(?:用|审|看)?\s*(?:它|这个)|"
    r"还是\s*审(?:查|核|计)?(?=$|[,，。！？;；])|"
    r"\b(?:use|review|audit|inspect)\s+it\b",
    re.IGNORECASE,
)

_BARE_COMMIT_RETRACTION_RE = re.compile(
    r"^\s*(?:(?:请|麻烦(?:你)?|帮我)\s*)?提交(?:一?下|吧)?\s*$",
    re.IGNORECASE,
)
_DIFF_REVIEW_RE = re.compile(
    r"(?:看|审(?:查|核|计|一?下|下)?|检查|review|audit|inspect|check)"
    r"[^。！？!?;；\n]{0,32}\b(?:git\s+)?diff\b|"
    r"\b(?:git\s+)?diff\b[^。！？!?;；\n]{0,32}"
    r"(?:看|审(?:查|核|计|一?下|下)?|检查|review|audit|inspect|check)",
    re.IGNORECASE,
)
_DEPENDENCY_PIN_REVIEW_RE = re.compile(
    r"(?:审(?:查|核|计|一?下|下)?|检查|review|audit|inspect|check)"
    r"[^。！？!?;；\n]{0,32}\b(?:current\s+)?pin\b"
    r"(?!\s*(?:码|code|number|password)(?:\b|$))",
    re.IGNORECASE,
)
_NON_DEPENDENCY_PIN_CONTEXT_RE = re.compile(
    r"(?:门禁|银行卡|解锁|密码)|"
    r"\b(?:access|bank|card|door|lock|password)\b",
    re.IGNORECASE,
)


_DISCOURSE_RETRACTION_RE = re.compile(
    r"(?:^|[.!?;,，。！？；—…])\s*"
    r"(?P<cue>"
    r"(?:nah\s+)?scratch\s+that|"
    r"(?:wait\s+no|never\s+mind|cancel\s+that|no)\b"
    r"(?=\s*(?:[-—,:：，。.!；;]+\s*)?"
    r"(?:(?:only|just|please)\s+)?(?:review|audit|inspect|check|explain|"
    r"analy[sz]e|read|search|fix|repair|write|update|refactor|optimize|"
    r"run|execute)\b)|"
    r"wait(?=\s*(?:[,;:—-]|$))|"
    r"actually|"
    r"算了|撤回|收回|"
    r"等等(?=\s*(?:[,，;；:：—.!。！？]|$))|"
    r"哦不|不对|不是这样|改主意了"
    r")\s*(?:[-—,:：，。.!；;]+\s*)?",
    re.IGNORECASE,
)
_UNPUNCTUATED_DISCOURSE_RETRACTION_RE = re.compile(
    r"(?P<cue>哦不|不对|不是这样|不是(?=\s*[,，:：])|改主意了|算了|等等)"
    r"\s*(?:[-—,:：，。.!；;]+\s*)?"
    r"(?=\s*(?:还是\s*)?(?:只|仅|别|不要|不用|不必|无需|无须)?\s*"
    r"(?:列|查|看|读|审|审查|检查|解释|说明|分析|跑|运行|执行|修|改|"
    r"写|补|更新|重构|优化|review\b|audit\b|inspect\b|check\b|"
    r"explain\b|analy[sz]e\b))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _IntentRetraction:
    superseded_end: int
    active_start: int


def _latest_intent_retraction(text: str) -> _IntentRetraction | None:
    """Return the final discourse correction that supersedes earlier work.

    A correction cue is authority-bearing only when an action precedes it.
    This keeps sentence-initial emphasis (``Actually, run pytest``) and the
    Chinese noun suffix ``等等`` out of the retraction lane.  ``actually`` is
    additionally bounded to a replacement action or an explicit prohibition.
    """

    matching_text = _mask_protected_intent_literals(text)
    matches: list[re.Match[str]] = [
        *_INTENT_REPLACEMENT_PREFIX_RE.finditer(matching_text),
        *_GENERAL_INTENT_REPLACEMENT_PREFIX_RE.finditer(matching_text),
    ]
    for match in [
        *_DISCOURSE_RETRACTION_RE.finditer(matching_text),
        *_UNPUNCTUATED_DISCOURSE_RETRACTION_RE.finditer(matching_text),
    ]:
        prefix = matching_text[: match.start()]
        if ACTION_MARKER_RE.search(prefix) is None:
            continue
        cue = match.group("cue").strip().lower()
        suffix = matching_text[match.end() :]
        if cue == "actually" and not (
            ACTION_MARKER_RE.search(suffix) is not None
            or effective_negation_matches(suffix)
        ):
            continue
        matches.append(match)
    if not matches:
        return None
    latest = max(matches, key=lambda match: (match.end(), match.start()))
    return _IntentRetraction(
        superseded_end=latest.start(),
        active_start=latest.end(),
    )


def _mask_superseded_intent_prefix(text: str) -> str:
    retraction = _latest_intent_retraction(text)
    if retraction is None:
        return text
    return " " * retraction.active_start + text[retraction.active_start :]


_CLAUSE_SEPARATOR_RE = re.compile(
    r"(?:[;；—\n]+|\s+(?=\bwithout\b)|"
    r"[,，]\s*(?=(?:请(?:你)?|帮我|麻烦(?:你)?|please\b|kindly\b))|"
    r"\s+\bwhile\b(?=\s+(?:fixing|repairing|solving|resolving)\b)|"
    r"\s+(?=\b(?:skip|avoid)\s+(?:(?:the|any|all|every)\s+)?"
    r"(?:integration|unit|smoke|contract|end[- ]to[- ]end|e2e)\s+"
    r"(?:tests?|suite)\b)|"
    r"\s+(?=\bnot\s+(?:the\s+)?"
    r"(?:integration|unit|smoke|contract|end[- ]to[- ]end|e2e)\s+tests?\b)|"
    r"\s+(?=\b(?:do\s+not|don['’]?t|dont)\b)|"
    r"\s+(?=\b(?:just|only)\s+(?:explain|describe|review|audit|inspect|"
    r"check|analy[sz]e|read|search|find|run|execute|write|update|fix|"
    r"repair|solve|resolve)\b)|"
    r"\s+(?=(?:代码|测试|git|文档|readme)\s*(?:不要|别|甭))|"
    r"\s+(?=只(?:审查|审核|检查|解释|说明|搜索|查找|运行|跑|修复|修改|更新))|"
    r"\s+[&＆+]\s+|"
    r"[,，]\s*(?=(?:只|再|然后|继续|接着|随后|先)?\s*"
    r"(?:定位|查明|找出|弄清|搞清|回答|看|分析|读(?:一?下)?|"
    r"运行(?!时)|执行(?!计划)|调用|"
    r"(?:直接)?(?:跑|修(?:复)?)|修改|更新|删除|重命名|"
    r"(?:普通|简单|单纯)?(?:解释|说明)|审查|检查|核对|复核|"
    r"列(?:出)?|罗列|比较|对比|测试|写|减少|消除|加入|新增|添加))|"
    r"[,，]\s*(?=(?:(?:这次|本次|本轮|这一轮|当前|现在|暂时|先|也)\s*)?"
    r"(?:不用|不必|无需|无须|不需要)\s*"
    r"(?:(?:再|真|真的|实际|直接|急着)\s*)*"
    r"(?:重构|优化|调整|修改|更新|改|修复|运行|执行|跑|测试|删除|删|"
    r"创建|生成|编写|写|审查|检查|解释|分析))|"
    r"[,，]\s*(?=(?:先)?(?:千万|绝对|现在|当前)?\s*"
    r"(?:别|不要|甭)\s*"
    r"(?:(?:真|真的|实际|急着|直接|替我|帮我|代我|做|进行)\s*)*"
    r"(?:碰|动|调整|改|修|跑|运行|执行|启动|写|删|挪|移动|重命名|rename\b|"
    r"审|查|看|定位|找|分析))|"
    r"[,，]\s*(?=(?:不做|不作|零)(?:任何)?(?:代码)?(?:改动|修改|变更))|"
    r"[,，]\s*(?=不\s*(?:挪|移动)(?:文件|目录|模块))|"
    r"[,，]\s*(?=(?:(?:不是|并非)在)?\s*(?:不\s*)?"
    r"(?:跑|运行|执行)(?:脚本|命令|测试|pytest|程序|代码))|"
    r"[,，]\s*(?=(?:不是|并非)(?:在)?(?:做|进行))|"
    r"[,，]\s*(?=(?:只是|仅仅是|仅是)\s*"
    r"(?:别|不要|甭|不用|不必|无需|无须))|"
    r"[,，]\s*(?=(?:只|仅)\s*基于[^,，。！？;；\n]{0,32}"
    r"(?:解释|说明|分析))|"
    r"[,，]\s*(?=(?:这|本|当前)?一轮\s*"
    r"(?:不|别)[^,，。！？;；\n]{0,8}(?:运行|执行|跑))|"
    r"[,，]\s*(?=只(?:做|进行)\s*(?:只读)?(?:安全)?"
    r"(?:审查|审核|审计|检查|评估|分析))|"
    r"[,，]\s*(?=(?:no|zero)\s+(?:code\s+)?(?:changes?|edits?|modifications?)\b)|"
    r"[,，]\s*(?=\b(?:do\s+not|don['’]?t|dont|never)\s+"
    r"(?:refactor|modify|update|change|edit|write|run|execute)\b)|"
    r"[,，]\s*(?=(?:完事|做完|完成后|之后)\s*(?:跑|运行|执行|复测|测))|"
    r"[,，]\s*(?=(?:ruff|flake8|mypy|lint(?:er)?).{0,16}(?:清|修|改|fix|clean))|"
    r"[,，]\s*(?=(?:只|再|然后|接着|随后|先)?\s*"
    r"(?:补|改(?:一下)?)(?![造善进]))|"
    r"[,，]\s*(?=(?:(?:please|continue(?:\s+to)?|then)\s+)?(?:直接\s*)?"
    r"(?:review|audit|inspect|check|analy[sz]e|explain|"
    r"describe|make|fix|patch|rewrite|repair|modify|update|change|"
    r"run|execute|search|find)\b)|"
    r"[,，]\s*(?=(?:马上|立即|现在|实际|直接)\s*"
    r"(?:重构|优化|调整|改|修改|更新|修|修复|补|patch\b|rewrite\b|fix\b))|"
    r"[,，]\s*(?=(?:光|只)(?:把|将))|"
    r"[。！？]\s*(?=(?:(?:如果)?(?:支持|可以)的话[,，]?\s*)?"
    r"(?:(?:现在|当前|直接|这次|本轮|接下来)\s*)?"
    r"(?:只|仅)?\s*"
    r"(?:请|帮我|麻烦)?\s*(?:运行|执行|调用|使用|修复|修改|更新|创建|"
    r"删除|移除|重命名|实现|编写|搜索|查找|定位|找出|解释|说明|分析|审查|检查))|"
    r"[。！？]\s*(?=先\s*(?:请|帮我|麻烦)?\s*分析)|"
    r"[。！？]\s*(?=(?:我(?:们)?\s*)?(?:还|现在|暂时|先)?\s*"
    r"(?:不准备|不打算|不需要|不用|不必|无需|无须|不要|别)\s*"
    r"(?:(?:再|真|真的|实际|直接|急着)\s*)*"
    r"(?:重构|优化|调整|修改|更新|改|修复|运行|执行|跑|测试|删除|删|"
    r"创建|生成|编写|写|审查|检查|解释|分析))|"
    r"[.!?]\s+(?=(?:(?:if\s+so|if\s+supported)[,]?\s+)?"
    r"(?:please\s+)?"
    r"(?:do\s+not|don't|review|audit|inspect|"
    r"check|fix|patch|rewrite|repair|modify|update|change|delete|remove|rename|run|"
    r"execute|test|search|find|explain|describe|identify|make|improve|"
    r"redesign|optimize|automate|document|write)\b)|"
    r"(?:[,，]\s*)?\b(?:and\s+then|and\s+finally|then|finally)\b|"
    r"(?<!anything )\bbut\b|"
    r"\band\b(?=\s+(?:do\s+not|don't|avoid|skip|exclude|refrain|without|"
    r"review|audit|inspect|check|fix|patch|rewrite|make|improve|redesign|optimize|"
    r"automate|document|write|"
    r"repair|solve|resolve|modify|update|change|delete|remove|rename|run|execute|test|"
    r"search|find|explain|describe)\b)|"
    r"(?:然后|接着|随后|顺便|最后|末了|完成后|确认后|"
    r"(?<!不)(?<!别)(?<!勿)(?<!不要)(?<!不用)(?<!不必)(?<!无需)(?<!无须)"
    r"(?<!请勿)(?<!禁止)(?<!不得)(?<!避免)(?<!我)再(?=\s*(?:查|看|瞅|讲|写|"
    r"扔|跑|把|给|只|别|不要|修|改|补|审|测|运行|执行|优化|减少|消除|"
    r"加入|新增|添加|核对|复核|比较|对比))|"
    r"并且|同时|但是|但|也(?=(?:不要|别|甭))|"
    r"(?:且|并|而)(?=(?:不|勿|未|避免|跳过|排除))|"
    r"并(?=(?:把[^，。；]{0,24})?(?:运行|执行|修复|修改|更新|测试|审查|检查|"
    r"删除|重命名|补|新增|添加|写|编写|改进|优化|重做|重新设计|自动化|记录|整理))))",
    re.IGNORECASE,
)


# Chinese users commonly omit both punctuation and spaces.  These anchors are
# deliberately grammar-shaped rather than a generic list of verbs: a boundary
# is introduced only for an explicit polarity/topic reset that follows an
# already observed action.  This keeps nouns such as ``README用法`` out of the
# action lane and avoids turning every ``先/再`` into an authority reset.
_UNPUNCTUATED_ACTION_BOUNDARY_RE = re.compile(
    r"(?=(?:"
    r"(?:代码|测试|git|文档|readme)\s*"
    r"(?:也\s*)?(?:不要|别|甭|不用|不必|无需|无须)|"
    r"(?:测试|单测|测例|用例)\s*(?:都|全部|一律)?\s*(?:跳过|略过)|"
    r"(?:(?:先|暂时|现在|当前|也)\s*)?"
    r"(?:不允许|不需要|不要|不用|不必|无需|无须|不能|不应|不可|"
    r"不准|不许|不得|别|甭|不)\s*(?:再\s*)?"
    r"(?:运行|执行|跑|修复|修|修改|更新|改|补|写|加|添|新增|添加|"
    r"创建|生成|删除|删)|"
    r"只(?:查|看|读|分析|审(?:查|核|计)?|解释|说明|搜索|查找|"
    r"运行|跑|修复|修改|更新|改|处理|列|测|补)|"
    r"只(?:把|将)|"
    r"只是(?=\s*(?:测试|用例)[^,，。！？;；\n]{0,16}"
    r"(?:不许|不得|不能|不要|别))|"
    r"复测(?=\s*(?:不必|无需|无须|不用|不要|不需要))|"
    r"修(?:好|掉)(?=[^,，。！？;；\n]{0,32}"
    r"(?:bug|问题|缺陷|故障))|"
    r"处理(?=[^,，。！？;；\n]{0,32}"
    r"(?:bug|问题|缺陷|故障|异常|错删|失效))|"
    r"(?:但\s*)?仍(?:然)?(?:要\s*)?(?:修复|修|解决|修改|更新|改)|"
    r"(?:把|将)[^,，。！？;；\n]{0,40}(?:修复|修好|修掉|解决|改|更新)|"
    r"直接(?:\s*(?:fix|repair|modify|update|change|edit|write|run|execute)\b|"
    r"把|将|修复|修|修改|更新|改|补|写|运行|执行|跑)|"
    r"(?:给我|帮我|请)\s*(?:解释|说明|审查|审核|审计)|"
    r"先测(?=[^,，。！？;；\n]{0,32}(?:启动|初始化|慢|耗时|延迟|瓶颈))|"
    r"就审(?:一遍|一?下|查|核|计)?|"
    r"(?:直接)?把[^,，。！？;；\n]{0,64}复现(?:成|为)(?:测试|用例|回归)"
    r"))",
    re.IGNORECASE,
)
_COORDINATED_TEST_DENIAL_RE = re.compile(
    r"(?:测试|单测|测例|用例)[^,，。！？;；\n]{0,12}"
    r"(?:既\s*)?不\s*(?:新增|添加|创建|编写|写|改)"
    r"\s*(?:也|且|又|并且?)\s*不\s*(?:执行|运行|跑)|"
    r"(?:测试|单测|测例|用例)\s*(?:一条|一行|一个)?\s*不\s*"
    r"(?:写|加|添|补|创建|新增)\s*[,，、]\s*"
    r"(?:一遍|一次|一回)?\s*不\s*(?:跑|执行|运行)",
    re.IGNORECASE,
)
_COORDINATED_ENGLISH_TEST_DENIAL_RE = re.compile(
    r"\bskip\s+(?:adding|creating|writing)\s+tests?\s+and\s+"
    r"skip\s+(?:running|executing)\s+(?:them|tests?)\b",
    re.IGNORECASE,
)
_COORDINATED_ENGLISH_TEST_REQUEST_RE = re.compile(
    r"\b(?:add|create|write)\s+and\s+(?:run|execute)\s+"
    r"(?:(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|regression|security|slow|smoke|system|unit)\s+tests?|"
    r"pytest\s+cases|tests?|test\s+cases?)\b",
    re.IGNORECASE,
)
_TEST_SCOPE_SUBTYPE_SUFFIX_RE = re.compile(
    r"(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|慢速|耗时|冒烟|"
    r"回归|系统|单元)\s*$",
    re.IGNORECASE,
)
_POSTFIX_TEST_EXECUTION_DENIAL_RE = re.compile(
    r"(?:测试|单测|测例|用例)\s*"
    r"(?:不要|别|甭|不用|不必|无需|无须|不许|不得|不准|禁止|"
    r"不能|不可|不允许)\s*(?:跑|运行|执行)",
    re.IGNORECASE,
)
_NEGATED_EXPLANATION_PREFIX_RE = re.compile(
    r"^\s*(?:别|不要|甭|不用|不必|无需|无须)\s*"
    r"(?:再)?(?:讲|解释|说明)(?:一?下|下)?"
    r"(?:大道理|空话|套话|原因|原理|过程|细节)?(?:了|啦)?\s*$",
    re.IGNORECASE,
)
_DOUBLE_NEGATION_BOUNDARY_PREFIX_RE = re.compile(
    r"(?:不用|不需要|不是|并非|并不是)\s*$",
    re.IGNORECASE,
)
_UNPUNCTUATED_READONLY_PREFIX_RE = re.compile(
    r"^\s*(?:只|仅)\s*(?:列|查|看|读|分析|审(?:查|核|计)?|解释|说明)"
    r"[^,，。！？;；\n]{0,48}\s*$",
    re.IGNORECASE,
)
_INSTRUCTIONAL_SUBJECT_FRAGMENT_RE = re.compile(
    r"(?:pytest(?:\s+参数)?|测试命令|单测命令|测试参数)",
    re.IGNORECASE,
)
_POSTPOSED_TEST_DENIAL_PREFIX_RE = re.compile(
    r"(?:测试|用例)[^,，。！？;；\n]{0,12}(?:也|都)?\s*$",
    re.IGNORECASE,
)
_POSTPOSED_TEST_DENIAL_SUFFIX_RE = re.compile(
    r"(?:也\s*)?(?:先\s*)?(?:不许|不得|不能|不要|别|不准|不允许)"
    r"[^,，。！？;；\n]{0,8}"
    r"(?:写|加|添|补(?!跑|充说明)|新增|添加|创建|生成|跑|运行|执行)",
    re.IGNORECASE,
)
_TEST_ARTIFACT_DENIAL_PREFIX_RE = re.compile(
    r"测试(?:代码|文件|用例)[^,，。！？;；\n]{0,12}(?:也|都)?\s*$",
    re.IGNORECASE,
)
_TEST_ARTIFACT_MODIFICATION_DENIAL_SUFFIX_RE = re.compile(
    r"(?:也\s*)?(?:不许|不得|不能|不要|别|不准|不允许)"
    r"[^,，。！？;；\n]{0,8}(?:动|改|修改|变更|碰)",
    re.IGNORECASE,
)
_DIRECT_ACTION_EXPLANATION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:请(?:你)?|帮我|麻烦(?:你)?)\s*)?"
    r"(?:解释|说明|讲(?:讲|解)?)(?:一?下)?"
    r"(?:为什么|为何|原因|风险|危险)?\s*$|"
    r"^\s*(?:please\s+)?(?:explain|describe)\s+(?:why|how)?\s*$",
    re.IGNORECASE,
)
_NOMINAL_TEST_DENIAL_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"\bno\s+(?:new|additional|extra)?\s*tests?|"
    r"\bwithout\s+(?:(?:any|all|the)\s+)?tests?|"
    r"\b(?:skip|avoid)\s+(?:(?:any|all|the)\s+)?"
    r"(?:(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|security|slow|smoke|system|unit)\s+)?tests?|"
    r"\b(?:(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|security|slow|smoke|system|unit)\s+)?tests?\s+"
    r"(?:are|is)\s+off[ -]?limits|"
    r"(?:先|只)?(?:跳过|略过|避开)\s*(?:所有|全部|任何)?\s*"
    r"(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|慢速|耗时|冒烟|"
    r"系统|单元)?\s*(?:测试|单测|测例|用例)|"
    r"(?:先)?(?:别|不准|不许|不得|禁止|不要|不用|不必|无需|无须)\s*"
    r"(?:再\s*)?(?:跑|运行|执行|写|加|添|新增|添加|创建|生成)\s*"
    r"(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|慢速|耗时|冒烟|"
    r"系统|单元)?\s*(?:测试|单测|测例|用例)|"
    r"(?:测试|单测|测例|用例|复测)[^,，。！？;；\n]{0,20}?"
    r"(?:不许|不得|不能|不要|别|不准|禁止|不允许|不必|无需|无须)"
    r"[^,，。！？;；\n]{0,8}?(?:写|加|添|新增|添加|创建|生成|跑|运行|"
    r"执行|做)|"
    r"(?:测试|单测|测例|用例)\s*(?:都|全部|一律)\s*(?:跳过|略过)"
    r")\s+"
    r"(?=(?:(?:please|now|only|just|while)\s+|只\s*)?"
    r"(?:fix|fixing|repair|repairing|resolve|resolving|solve|solving|"
    r"修复|修好|修掉|修|解决|修改|更新|改)(?![a-z0-9_]))",
    re.IGNORECASE,
)


_LEADING_NEGATED_SEQUENCE_RE = re.compile(
    r"^\s*(?:不要|别|甭|不用|不需要|不必|无需|无须|禁止|不得)\s*"
    r"先[^,，。！？;；\n]*$",
    re.IGNORECASE,
)
_STRUCTURAL_MOVE_SUBJECT_RE = re.compile(
    r"\bmove\b[^.!?;\n]{0,48}\b(?:files?|modules?|packages?)\b\s*$",
    re.IGNORECASE,
)
_STRUCTURAL_REFERENCE_UPDATE_RE = re.compile(
    r"^\s*(?:update|change|fix)\b[^.!?;\n]{0,32}"
    r"\b(?:imports?|refs?|references?)\b",
    re.IGNORECASE,
)
_CAPABILITY_QUESTION_RE = re.compile(
    r"(?:这个|该|本)?(?:系统|工具|模型|助手|代理|agent)"
    r"[^。！？?\n]{0,24}(?:是否支持|能否|有没有能力|有无能力|是否有能力)"
    r"[^。！？?\n]{0,120}[?？]?|"
    r"^\s*(?:does\s+(?:this|the)\s+(?:system|tool|model|assistant|agent)"
    r"\s+support\b|are\s+you\s+able\s+to\b)"
    r"[^.!?\n]{0,120}[?]?|"
    r"^\s*(?:你|您)(?:能|会|可以)(?:否)?\s*"
    r"(?:使用|调用|访问|支持|提供|进行)"
    r"[^。！？?\n]{0,80}(?:功能|能力|工具|接口|技能|skill)"
    r"\s*(?:吗|么|呢)[?？]?\s*$",
    re.IGNORECASE,
)
_SYSTEM_CATALOG_QUERY_RE = re.compile(
    r"^\s*"
    r"(?=[^\n\u3002\uff01\uff1f!?]{0,240}"
    r"(?:intents?|intend\b|intentions?|capabilit(?:y|ies)|tools?|skills?|"
    r"\u610f\u56fe|\u80fd\u529b|\u5de5\u5177|\u6280\u80fd))"
    r"(?=[^\n\u3002\uff01\uff1f!?]{0,240}"
    r"(?:intents?|intend\b|intentions?|\u610f\u56fe|"
    r"(?:this|the)\s+(?:system|assistant|agent|router)\b|"
    r"\b(?:your|assistant|agent|router|routing)\b|"
    r"(?:\u8fd9\u4e2a|\u8be5|\u672c)\u7cfb\u7edf|\u4f60(?:\u7684)?|\u52a9\u624b|\u4ee3\u7406|\u8def\u7531))"
    r"(?=[^\n\u3002\uff01\uff1f!?]{0,240}"
    r"(?:current(?:ly)?|available|supported|registered|exposed|"
    r"(?:this|the)\s+system|your|assistant|agent|router|routing|"
    r"\u76ee\u524d|\u5f53\u524d|\u73b0\u6709|\u53ef\u7528|\u652f\u6301(?:\u7684)?|\u5df2\u6ce8\u518c|\u6ce8\u518c\u7684|"
    r"(?:\u8fd9\u4e2a|\u8be5|\u672c)\u7cfb\u7edf|\u4f60(?:\u7684)?|\u52a9\u624b|\u4ee3\u7406|\u8def\u7531))"
    r"(?=[^\n\u3002\uff01\uff1f!?]{0,240}?"
    r"(?P<query>\b(?:list|show|display|enumerate|inventory|what|which)\b|"
    r"\u5217(?:\u51fa|\u51fa\u6765|\u4e00?\u4e0b|\u4e2a\u6e05\u5355)|\u7f57\u5217|\u5c55\u793a|\u663e\u793a|"
    r"\u76d8\u70b9|\u6709\u54ea\u4e9b|\u90fd\u6709\u4ec0\u4e48))"
    r"(?![^\n\u3002\uff01\uff1f!?]{0,240}(?:\u5de5\u5177\u8c03\u7528|\btool\s+calls?\b))"
    r"[^\n\u3002\uff01\uff1f!?]{1,240}[?\uff1f]?\s*$",
    re.IGNORECASE,
)
_NOISY_SYSTEM_CATALOG_QUERY_RE = re.compile(
    r"^\s*"
    r"(?=[^\n。！？!?]{0,240}(?:\b(?:intents?|intend|capablity|"
    r"capabilites|capabilities|skills?|tools?)\b|意图|能力|工具|技能))"
    r"(?=[^\n。！？!?]{0,240}(?:\b(?:current|avail(?:able)?|rn|mapping)\b|"
    r"目前|当前|现有|可用|支持|映射|这个|系统|路由))"
    r"(?=[^\n。！？!?]{0,240}?(?P<query>\b(?:wut|gimme|list|show|what|"
    r"which|inventory)\b|列一哈|列下|列一下|盘一下|盘点|有哪些|都有啥))"
    r"[^\n。！？!?]{1,240}[?？]?\s*$",
    re.IGNORECASE,
)
_TERSE_SYSTEM_CATALOG_QUERY_RE = re.compile(
    r"^\s*(?=[^\n。！？!?]{0,160}(?:\bintents?\b|意图))"
    r"(?=[^\n。！？!?]{0,160}(?:\bcapabilit(?:y|ies)\b|能力))"
    r"[^\n。！？!?]{0,140}?(?P<query_zh>有哪些|都有(?:什么|啥)|各有什么)"
    r"[^\n。！？!?]{0,20}[?？]?\s*$|"
    r"^\s*(?P<query_en>what\s+can)\s+"
    r"(?:u|you|this\s+(?:assistant|agent|system)|the\s+(?:assistant|agent|system))"
    r"\s+do(?:\s+(?:rn|now|right\s+now))?"
    r"(?:\s*[^\w\s?？]{0,4})?\s*[?？]?\s*$|"
    r"^\s*(?P<query_skill>skills?)\s*[?？]\s*$|"
    r"^\s*(?P<query_terse>(?:"
    r"(?:能|可)(?:够)?识别(?:的)?\s*(?:intents?|意图)"
    r"(?:\s*名称)?\s*(?:逐个)?(?:报|列|说)(?:一?下)?|"
    r"(?:capabilit(?:y|ies)|能力)\s*(?:清单|列表)"
    r"\s*(?:展开)?(?:看(?:看)?|列(?:一?下)?|说(?:一?下)?)|"
    r"(?:agent|assistant|系统|助手|代理)\s*"
    r"(?:注册(?:的|了)?|现有(?:的)?)\s*(?:skills?|技能)"
    r"\s*(?:有)?(?:几(?:项|个)|多少)"
    r"))\s*[?？]?\s*$",
    re.IGNORECASE,
)
_SYSTEM_CATALOG_QUERY_FRAGMENT_RE = re.compile(
    r"(?:^|[。！？!?;；…]+)\s*"
    r"(?P<fragment>"
    r"(?=[^。！？!?;；\n]{0,180}(?:\b(?:intents?|intend|"
    r"capabilit(?:y|ies)|skills?|tools?)\b|意图|能力|技能|工具))"
    r"(?=[^。！？!?;；\n]{0,180}(?:\b(?:system|assistant|agent|router|"
    r"routing|registry|current|available|registered|supported)\b|"
    r"系统|助手|代理|路由|目前|当前|现有|可用|注册|支持|识别))"
    r"[^。！？!?;；\n]{0,160}?"
    r"(?P<query_fragment>哪些|都有(?:什么|啥)|有啥|注册了啥|清单|"
    r"\b(?:list|show|what|which|inventory)\b)"
    r"[^。！？!?;；\n]{0,40})",
    re.IGNORECASE,
)
_ORDINARY_PHYSICAL_TOOL_DOMAIN_RE = re.compile(
    r"\b(?:hardware\s+store|toolbox|workbench|power\s+tool|hand\s+tool)\b",
    re.IGNORECASE,
)
_SYSTEM_CATALOG_REQUESTED_WORK_RE = re.compile(
    r"^\s*(?:(?:please|can\s+you|could\s+you|would\s+you)\s+)?"
    r"(?:run|execute|invoke|apply|use|fix|repair|modify|update|create|"
    r"delete|remove|rename|implement|write|search|find)\b|"
    r"\b(?:and(?:\s+then)?|then|afterwards?)\b\s*(?:please\s+)?"
    r"(?:run|execute|invoke|apply|use|fix|repair|modify|update|create|"
    r"delete|remove|rename|implement|write|search|find)\b|"
    r"^\s*(?:\u8bf7|\u5e2e\u6211|\u9ebb\u70e6)?\s*(?:\u8fd0\u884c|\u6267\u884c|\u8c03\u7528|\u4f7f\u7528|\u5e94\u7528|\u4fee\u590d|"
    r"\u4fee\u6539|\u66f4\u65b0|\u521b\u5efa|\u5220\u9664|\u79fb\u9664|\u91cd\u547d\u540d|\u5b9e\u73b0|\u7f16\u5199|\u641c\u7d22|\u67e5\u627e)|"
    r"(?:\u7136\u540e|\u968f\u540e|\u63a5\u7740|\u518d|\u5e76\u4e14|\u5e76|\u540e)\s*(?:\u8bf7)?\s*"
    r"(?:\u8fd0\u884c|\u6267\u884c|\u8c03\u7528|\u4f7f\u7528|\u7528|\u5e94\u7528|\u4fee\u590d|\u4fee\u6539|\u66f4\u65b0|\u521b\u5efa|"
    r"\u5220\u9664|\u79fb\u9664|\u91cd\u547d\u540d|\u5b9e\u73b0|\u7f16\u5199|\u641c\u7d22|\u67e5\u627e)",
    re.IGNORECASE,
)
_CONTINUATION_RE = re.compile(
    r"^(?:请)?(?:(?:继续|接着|照旧|按刚才的)(?=$|[\s，。！？,.!?]|"
    r"查|看|检查|处理|修复|修改|做|核对|审查|分析|优化)|"
    r"(?:go\s+on|continue|keep\s+going)\b)|"
    r"(?:继续|接着)(?:查|看|检查|处理|修复|修改|做|核对|审查|分析|优化)",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"\b(?:it|this|that|them|those|same|above|previous)\b|"
    r"(?:它|这个|这段|该文件|这个文件|刚才|结论|发现|上面|前述)",
    re.IGNORECASE,
)
_LOCAL_DISCOURSE_REFERENCE_RE = re.compile(
    r"(?:这个|那个|该)\s*(?:词|词语|术语|说法)|"
    r"\b(?:this|that)\s+(?:word|term|phrase)\b",
    re.IGNORECASE,
)
_TECHNICAL_MARKER_RE = re.compile(
    r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml)\b|"
    r"\b(?:code|program|function|method|class|module|api|auth|authentication|login|checkout|"
    r"test|pytest|fixture|ci|cli|repository|repo|readme|router|routing|parser|skill|implementation|"
    r"command|process|job|build|startup|"
    r"worker|pool|thread|lock|deadlock|concurrency|race|contention|"
    r"config|root|directory|database|query|sql|token)\b|"
    r"(?:代码|函数|方法|类|模块|接口|登录|鉴权|测试|配置|路由|解析器|实现|"
    r"逻辑|程序|注释|文件|根目录|页面|组件|布局|数据库|查询|摘要|记忆|部署|分支|提交|"
    r"线程|锁|死锁|竞争|并发)",
    re.IGNORECASE,
)
_EVERYDAY_NON_SOFTWARE_SUBJECT_RE = re.compile(
    r"\b(?:car|vehicle|coffee\s+machine|coffee\s+maker|appliance|"
    r"running|jogging|swimming|hiking|trails?|workout|heart\s*rate|pace|running\s+shoes?|"
    r"puppy|dog|pet|parrot|bird|sofa|couch|photo\s+album|family\s+album|"
    r"swim(?:ming)?\s+goggles?|visa\s+application|factory\s+(?:shift|schedule)|"
    r"branch\s+office|home\s+sales?|walking\s+challenge|travel\s+photos?)\b|"
    r"(?:汽车|车辆|车子|我的车|咖啡机|家电|跑步|跑鞋|泳镜|五公里|游泳|健身|"
    r"锻炼|运动|心率|配速|小狗|狗狗|宠物|鹦鹉|鸟|沙发|相册|签证申请|"
    r"工厂[^。！？!?;；\n]{0,12}排班|旅游照片|旅行照片|房屋销售)",
    re.IGNORECASE,
)
_STRONG_SOFTWARE_SUBJECT_RE = re.compile(
    r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|json|ya?ml|toml)\b|"
    r"\b(?:app|api|code|program|function|method|class|module|service|server|"
    r"database|query|sql|repository|repo|router|parser|pytest|tests?)\b|"
    r"(?:代码|程序|函数|方法|类|模块|接口|服务|数据库|查询|仓库|路由|"
    r"解析器|测试)",
    re.IGNORECASE,
)
_CONTEXT_ENGINEERING_TOPIC_VOCABULARY_RE = {
    "git-workflow": re.compile(
        r"\b(?:git|repos?|repository|repositories|commits?|history|"
        r"branch(?:es)?|release|"
        r"version\s+control|workflows?|cleanup)\b|"
        r"(?:代码仓库|仓库|版本控制|开发分支|分支|提交历史|提交|历史|"
        r"工作流|流程|边界|清理)",
        re.IGNORECASE,
    ),
    "agent-orchestration": re.compile(
        r"\b(?:multi[- ]agent|sub[- ]?agent|worker\s+agent|agents?|"
        r"orchestration|scheduling|scheduler|dispatch|workflows?|systems?|"
        r"workers?|delegation|parallel|"
        r"routing|code|modules?|software\s+tasks?|tasks?)\b|"
        r"(?:多智能体|多代理|子智能体|子代理|多工作单元|工作单元|智能体|代理|"
        r"编排|调度|并行|任务(?:分配|拆分|委派|执行|调度)?|工作流|流程|系统|"
        r"方案|路由|代码|模块|分工|委派)",
        re.IGNORECASE,
    ),
    "deployment-automation": re.compile(
        r"\b(?:ci(?:\s*/\s*cd)?|kubernetes|deployment|deploy|release|automation|"
        r"automated|canary|pipelines?|workflows?|services?|applications?|"
        r"clusters?|containers?|images?|environments?|staging|production|"
        r"rollback|rollout|plans?)\b|"
        r"(?:自动灰度发布|发布自动化|自动化部署|部署自动化|生产|部署|发布|灰度|"
        r"自动化|流水线|工作流|流程|环境|服务|应用|集群|容器|镜像|版本|"
        r"生产环境|预发布|回滚|放量|上线|方案)",
        re.IGNORECASE,
    ),
}
_CONTEXT_ENGINEERING_TOPIC_FILLER_RE = re.compile(
    r"\b(?:implement(?:ation)?|build|create|design|configure|land|apply|"
    r"execute|split|organize|review|audit|inspect|analy[sz]e|clean(?:\s+up)?|"
    r"the|an?|and|with|for|of|to|our|this|that|please|now|project)\b|"
    r"(?:实施|落地|配置|构建|整理|拆分|梳理|查看|审查|审计|分析|核对|回顾|"
    r"导出|总结|并|和|与|的|任务|项目)",
    re.IGNORECASE,
)


def _context_topic_is_engineering_grounded(text: str, topic: str) -> bool:
    """Require ambiguous context aliases to carry engineering evidence."""

    vocabulary = _CONTEXT_ENGINEERING_TOPIC_VOCABULARY_RE.get(topic)
    if vocabulary is None:
        return True
    projected = _affirmed_evidence_projection(text).strip(" \t\r\n,，。！？!?;；")
    remainder = vocabulary.sub(" ", projected)
    remainder = _CONTEXT_ENGINEERING_TOPIC_FILLER_RE.sub(" ", remainder)
    remainder = re.sub(r"[\s,，。！？!?;；:：/\\()（）._-]+", " ", remainder)
    if not _has_meaningful_context_residual(remainder):
        return True

    # Engineering task frames are frequently compositional: ``repository +
    # release automation`` and ``multi-agent + delegation pipeline`` span two
    # routing topics.  Validate the frame against the union of the bounded
    # engineering vocabularies before rejecting it.  Non-engineering tails
    # such as ``workshop agenda`` or ``museum exhibit`` remain as residuals and
    # therefore still fail closed.
    composed_remainder = projected
    for engineering_vocabulary in _CONTEXT_ENGINEERING_TOPIC_VOCABULARY_RE.values():
        composed_remainder = engineering_vocabulary.sub(" ", composed_remainder)
    composed_remainder = _CONTEXT_ENGINEERING_TOPIC_FILLER_RE.sub(
        " ", composed_remainder
    )
    composed_remainder = re.sub(
        r"[\s,，。！？!?;；:：/\\()（）._-]+",
        " ",
        composed_remainder,
    )
    return not _has_meaningful_context_residual(composed_remainder)

# Domain exclusions are not action negations.  In ``rename the family album,
# not a module`` the word ``module`` must not become positive evidence for the
# earlier rename, while ``this is not a bug; inspect auth.py`` must retain the
# later inspection.  These spans are therefore masked only for authority and
# routing evidence; the original text remains available for diagnostics.
_DOMAIN_EXCLUSION_RE = re.compile(
    r"(?:不是|并非|而不是|而非|不属于)\s*(?:在\s*)?"
    r"(?:软件配置|软件文档|软件代码|软件测试|software\s+test|pytest|tests?|"
    r"(?:跑|运行|执行)(?:软件)?测试|"
    r"代码设计(?:评审)?|(?:重命名|移动|重构)?(?:代码)?(?:类|模块|符号|包)|"
    r"代码|程序|配置|文件|脚本|软件)|"
    r"\b(?:(?:this|that|it)\s+(?:is|was)\s+|"
    r"(?:these|those|they)\s+(?:are|were)\s+|"
    r"(?:is|are|was|were)\s+)?not\s+(?:an?\s+|the\s+)?"
    r"(?:(?:renamed|moved|refactored|software)\s+)?"
    r"(?:software\s+)?(?:config(?:uration)?|document(?:ation)?|code|test)|"
    r"\b(?:(?:this|that|it)\s+(?:is|was)\s+|"
    r"(?:these|those|they)\s+(?:are|were)\s+|"
    r"(?:is|are|was|were)\s+)?not\s+(?:an?\s+|the\s+)?"
    r"(?:renamed\s+|moved\s+|refactored\s+|software\s+)?"
    r"(?:class|module|symbol|package|program|file|script|软件测试|类|模块|符号|包)\b",
    re.IGNORECASE,
)
_AUTHORITY_SCOPE_RESET_RE = re.compile(
    r"[,，;；。！？!?\n]|\b(?:but|instead|rather)\b|(?:但是|而是|但)",
    re.IGNORECASE,
)
_NON_AUTHORITY_DISCOURSE_DECLINE_RE = re.compile(
    r"^\s*(?:(?:先|暂时)\s*)?(?:不用了|不用啦|算了)"
    r"(?:\s+|[,，。.!；;]+\s*)",
    re.IGNORECASE,
)


def _affirmed_evidence_projection(text: str) -> str:
    """Mask non-authorizing spans without changing source offsets.

    This is the shared positive-evidence view used by deterministic matching,
    semantic admission, and Skill routing.  Explicit negative operations are
    still parsed from the original text; their targets simply cannot lend
    positive domain evidence to another operation.
    """

    masked = list(_mask_protected_intent_literals(text))
    matching_text = "".join(masked)
    spans = [match.span() for match in _DOMAIN_EXCLUSION_RE.finditer(matching_text)]
    discourse_declines = tuple(
        _NON_AUTHORITY_DISCOURSE_DECLINE_RE.finditer(matching_text)
    )
    for negation in effective_negation_matches(matching_text):
        if any(
            decline.start() <= negation.start() < decline.end()
            for decline in discourse_declines
        ):
            continue
        reset = _AUTHORITY_SCOPE_RESET_RE.search(
            matching_text,
            negation.end(),
        )
        spans.append(
            (
                negation.start(),
                reset.start() if reset is not None else len(matching_text),
            )
        )
    for start, end in spans:
        for index in range(start, end):
            if masked[index] not in "\r\n":
                masked[index] = " "
    return "".join(masked)


def _has_affirmed_software_subject(text: str) -> bool:
    """Detect software evidence after removing the current action wording."""

    projected = _affirmed_evidence_projection(text)
    return _STRONG_SOFTWARE_SUBJECT_RE.search(
        ACTION_MARKER_RE.sub(" ", projected)
    ) is not None


def _technical_evidence_polarity(text: str) -> tuple[bool, bool]:
    """Return ``(affirmed, excluded)`` technical-target evidence."""

    matching_text = _mask_protected_intent_literals(text)
    projected = _affirmed_evidence_projection(matching_text)
    affirmed = False
    excluded = False
    for marker in _TECHNICAL_MARKER_RE.finditer(matching_text):
        start, end = marker.span()
        if projected[start:end].strip():
            affirmed = True
        else:
            excluded = True
    return affirmed, excluded
_PENDING_SUBJECT_RE = re.compile(
    r"\b(?:fail(?:ed|ure)?|error|broken|slow|timeout|deadlock|race|contention)\b|"
    r"\b(?:takes?|spends?)\b[^.!?;\n]{0,24}"
    r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|seconds?|minutes?)\b|"
    r"(?:失败|报错|异常|超时|变慢|很慢|偶发|死锁|竞争|"
    r"红了|挂了|抽风|炸了|磨磨唧唧|越跑越肉)",
    re.IGNORECASE,
)
_PENDING_TEST_FAILURE_RE = re.compile(
    r"(?:\b(?:pytest|pytes)\b|(?:单元|集成|回归)?测试|测试用例|断言)"
    r"[^\u3002\uff01\uff1f!?;\uff1b\n]{0,40}"
    r"(?:失败|报错|异常|红了|挂了|抽风|炸了)",
    re.IGNORECASE,
)
_PENDING_BUG_REPORT_RE = re.compile(
    r"(?:根据|来自)?(?:用户|客户)[^。！？!?;\uff1b\n]{0,16}"
    r"(?:报告|反馈|报障)[^。！？!?;\uff1b\n]{0,24}"
    r"(?:\bbugs?\b|缺陷|问题)|"
    r"\b(?:user|customer)[-\s]+reported\b[^.!?;\n]{0,24}\bbugs?\b",
    re.IGNORECASE,
)
_IMPLEMENTATION_WITH_TESTS_RE = re.compile(
    r"\b(?:implement|build|develop)\b[^.!?;\n]{0,64}"
    r"\b(?:with|including)\b[^.!?;\n]{0,24}"
    r"\b(?:test\s+coverage|tests?)\b",
    re.IGNORECASE,
)
_REPORTED_FAILURE_CONTEXT_RE = re.compile(
    r"(?:根据|来自)?(?:用户|客户)[^。！？!?;；\n]{0,16}"
    r"(?:报告|反馈|报障|说明|说)[^。！？!?;；\n]{0,80}"
    r"(?:失败|报错|异常|错误|崩溃|\bbugs?\b|缺陷|问题)|"
    r"\b(?:users?|customers?)\s+"
    r"(?:report(?:ed|s|ing)?|say|said)\b[^.!?;\n]{0,100}"
    r"\b(?:fails?|failed|failure|errors?|crash(?:es|ed)?|bugs?|issues?|problems?|"
    r"wrong|incorrect|unexpected)\b",
    re.IGNORECASE,
)
_TRAILING_ACTION_REVOCATION_RE = re.compile(
    r"(?:(?:就|也|还是|暂时|先)\s*)?"
    r"(?:不用|不必|无需|无须|不需要)(?:再)?(?:做|处理)?(?:了|啦)?(?:吧)?"
    r"[。.!！]*\s*$|"
    r"(?:(?:任务|请求|计划|操作|这一步|这件事)\s*"
    r"(?:(?:就|也|还是|暂时|先)\s*)?|(?:先|暂时|现在)\s*)"
    r"(?:取消|撤回|收回)(?:了|啦)?(?:吧)?"
    r"[。.!！]*\s*$|"
    r"(?:is\s+)?(?:no\s+longer\s+needed|not\s+needed|unnecessary)"
    r"[.!]*\s*$",
    re.IGNORECASE,
)
_TEST_CREATION_DENIAL_RE = re.compile(
    r"(?:不需要|无需|无须|不必)\s*(?:回归|单元|集成)?"
    r"(?:测试用例|测例|用例)|"
    r"\bneither\s+(?:add|create|write)\s+nor\s+(?:run|execute)\s+"
    r"(?:pytest\s+cases|tests?|test\s+cases?)\b|"
    r"\b(?:tests?|pytest\s+cases)\s*:\s*neither\s+"
    r"(?:add|create|write)\s+nor\s+(?:run|execute)\b|"
    r"\bno\s+test\s+creation\s+and\s+no\s+test\s+execution\b|"
    r"\bskip\s+(?:adding|creating|writing)\s+tests?\s+and\s+"
    r"skip\s+(?:running|executing)\s+(?:them|tests?)\b|"
    r"\bdo\s+not\s+(?:create|add|write)\s+or\s+"
    r"(?:run|execute)\s+tests?\b|"
    r"\bwithout\s+(?:writing|creating|adding)\s+tests?\s+or\s+"
    r"(?:running|executing)\s+(?:them|tests?)\b|"
    r"\b(?:tests?|test\s+cases?)\s+(?:must\s+|should\s+)?neither\s+"
    r"(?:be\s+)?(?:created|written|added)\s+nor\s+(?:be\s+)?"
    r"(?:run|executed)\b|"
    r"\bneither\s+(?:create|add|write)\s+"
    r"(?:pytest\s+cases|tests?|test\s+cases?)\s+nor\s+"
    r"(?:run|execute)\s+(?:them|those|the\s+tests?)\b|"
    r"\bwithout\s+(?:creating|adding|writing|modifying)\s+"
    r"(?:or|and)\s+(?:running|executing)\s+tests?\b|"
    r"\b(?:leave|leaving)\s+(?:the\s+)?(?:tests?|test\s+suite)\s+"
    r"unwritten\b|"
    r"\bno\s+(?:new|additional|extra)?\s*tests?\b|"
    r"\bno\s+(?:new|additional|extra)?\s*"
    r"(?:(?:regression|unit|integration)\s+)?(?:tests?|cases?|test\s+cases?)\b|"
    r"\bavoid\s+(?:creating|adding|writing|changing|modifying)\s+"
    r"(?:(?:any|the)\s+)?tests?\b|"
    r"\bavoid(?:ing)?\s+(?:any\s+)?"
    r"(?:changes?|modifications?|edits?)\s+to\s+"
    r"(?:the\s+)?(?:test\s+files?|test\s+suite|tests?)\b|"
    r"\b(?:leave|leaving)\s+(?:the\s+)?"
    r"(?:test\s+files?|test\s+suite|tests?)\s+"
    r"(?:untouched|unchanged|unmodified)\b|"
    r"\btests?(?:\s+suite)?\b.{0,24}\b(?:must\s+)?(?:stay|remain)\s+"
    r"(?:untouched|unchanged|unmodified)\b|"
    r"测试(?:代码|文件|用例)?[^,，。！？;；\n]{0,12}"
    r"(?:保持|维持)(?:原样|不变)|"
    r"测试(?:代码|文件|用例)?[^,，。！？;；\n]{0,12}"
    r"(?:不许|不得|不能|不要|别|不准|不允许)\s*"
    r"(?:动|改|修改|变更|碰)|"
    r"(?:集成|单元|冒烟|慢速|耗时|契约|端到端)\s*(?:测试|用例)"
    r"(?:先)?\s*(?:不许|不得|不能|不要|别|不准|不允许)\s*"
    r"(?:写|加|添|新增|添加|创建|生成)|"
    r"(?:测试|用例)[^,，。！？;；\n]{0,12}"
    r"(?:不许|不得|不能|不要|别|不准|不允许)[^,，。！？;；\n]{0,8}"
    r"(?:写|加|添|补(?!跑|充说明)|新增|添加|创建|生成)|"
    r"(?:测试|单测|测例|用例)(?:用例)?\s*"
    r"(?:不许|不得|不能|不要|别|不准|不允许|不)\s*"
    r"(?:补(?!跑|充说明)|写|加|添|新增|添加|创建|生成)|"
    r"(?:不要|别|不许|不得|不准|不允许)\s*"
    r"(?:新建|新增|添加|创建|编写|补(?!跑|充说明)|写|加|添)\s*"
    r"(?:任何|新的|新)?\s*(?:回归|单元|集成)?\s*"
    r"(?:测试|单测|测例|用例)|"
    r"(?:测试|单测|测例|用例)(?:代码|文件|用例)?\s*"
    r"(?:不要|别|不许|不得|不准|不允许)\s*"
    r"(?:动|改|修改|变更|碰)|"
    r"(?:测试|单测|测例|用例)\s*(?:一条|一行|一个)?\s*不\s*"
    r"(?:写|加|添|补|创建|新增)|"
    r"\bnot\s+(?:the\s+)?"
    r"(?:integration|unit|smoke|contract|end[- ]to[- ]end|e2e)\s+tests?\b",
    re.IGNORECASE,
)
_TEST_EXECUTION_DENIAL_RE = re.compile(
    r"\bneither\s+(?:add|create|write)\s+nor\s+(?:run|execute)\s+"
    r"(?:pytest\s+cases|tests?|test\s+cases?)\b|"
    r"\b(?:tests?|pytest\s+cases)\s*:\s*neither\s+"
    r"(?:add|create|write)\s+nor\s+(?:run|execute)\b|"
    r"\bno\s+test\s+creation\s+and\s+no\s+test\s+execution\b|"
    r"\bskip\s+(?:adding|creating|writing)\s+tests?\s+and\s+"
    r"skip\s+(?:running|executing)\s+(?:them|tests?)\b|"
    r"\bdo\s+not\s+(?:create|add|write)\s+or\s+"
    r"(?:run|execute)\s+tests?\b|"
    r"\bwithout\s+(?:writing|creating|adding)\s+tests?\s+or\s+"
    r"(?:running|executing)\s+(?:them|tests?)\b|"
    r"\b(?:tests?|test\s+cases?)\s+(?:must\s+|should\s+)?neither\s+"
    r"(?:be\s+)?(?:created|written|added)\s+nor\s+(?:be\s+)?"
    r"(?:run|executed)\b|"
    r"\bneither\s+(?:create|add|write)\s+"
    r"(?:pytest\s+cases|tests?|test\s+cases?)\s+nor\s+"
    r"(?:run|execute)\s+(?:them|those|the\s+tests?)\b|"
    r"\b(?:leave|leaving)\s+(?:the\s+)?"
    r"(?:pytest|tests?|test\s+suite)\s+(?:unrun|unexecuted|not\s+run)\b|"
    r"\b(?:leave|leaving)\s+(?:the\s+)?(?:tests?|test\s+suite)\s+"
    r"unwritten\s+and\s+unexecuted\b|"
    r"\b(?:tests?|test\s+suite)\s+(?:must|should|can|may)\s+"
    r"(?:never\s+(?:be\s+)?(?:run|executed)|"
    r"not\s+(?:be\s+)?(?:run|executed))\b|"
    r"\bno\s+(?:test|pytest)\s+(?:runs?|execution)\b|"
    r"\bwithout\s+(?:creating|adding|writing|modifying)\s+"
    r"(?:or|and)\s+(?:running|executing)\s+tests?\b|"
    r"\bwithout\s+(?:(?:any|all|the)\s+)?(?:tests?|test\s+suite)\b|"
    r"\bwithout\s+(?:running|executing)\s+"
    r"(?:(?:any|the|all|every)\s+)?(?:(?:entire|whole)\s+)?"
    r"(?:test\s+suite|tests?|pytest)\b|"
    r"\bavoid\s+(?:running|executing)\s+"
    r"(?:(?:any|the|all|every)\s+)?(?:(?:entire|whole)\s+)?"
    r"(?:test\s+suite|tests?)\b|"
    r"\b(?:without|skip|avoid)\s+(?:running|executing)\s+"
    r"(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|regression|security|slow|smoke|system|unit)\s+"
    r"(?:tests?|suite)\b|"
    r"\b(?:skip|avoid)\s+(?:the\s+|any\s+|all\s+|every\s+)?"
    r"(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|security|slow|smoke|system|unit)\s+"
    r"(?:tests?|suite)\b|"
    r"(?:不|不要|别|无需|无须|不必|不需要)\s*(?:再\s*)?(?:跑测|复测)|"
    r"\b(?:acceptance|browser|contract|e2e|end[- ]to[- ]end|integration|"
    r"load|performance|security|slow|smoke|system|unit)\s+tests?\s+"
    r"(?:are|is)\s+off[ -]?limits\b|"
    r"(?:验收|浏览器|契约|端到端|集成|负载|性能|安全|慢速|耗时|冒烟|"
    r"系统|单元)\s*(?:测试|用例)(?:先)?\s*"
    r"(?:跳过|避开|(?:不许|不得|不能|不要|别|不准|不允许)\s*"
    r"(?:执行|运行|跑))|"
    r"\b(?:skip|avoid)\s+(?:(?:any|all|the)\s+)?"
    r"(?:tests?|test\s+suite)\b"
    r"(?!\s+(?:reports?|plans?|summaries|results?|documentation)\b)|"
    r"(?:\btests?(?:\s+suite)?\b|(?:测试|单测|测例|用例))\s*"
    r"(?:(?:are|is)\s+)?"
    r"(?:(?:completely|strictly|totally|entirely)\s+)?off[ -]?limits\b|"
    r"\btests?(?:\s+suite)?\b.{0,16}"
    r"\b(?:stay|remain)\s+(?:unrun|not\s+run|unexecuted)\b|"
    r"\b(?:tests?|pytest)\s+(?:do\s+not|don['’]?t|no|never|skip)\s*"
    r"(?:run|execute)?\b|"
    r"(?:跳过|略过)\s*(?:所有|全部|任何|验收|浏览器|契约|端到端|"
    r"集成|负载|性能|安全|慢速|耗时|冒烟|回归|系统|单元)?\s*"
    r"(?:测试|单测|测例|用例)|"
    r"(?:测试|单测|测例|用例)\s*(?:都|全部|一律)?\s*"
    r"(?:skip(?:掉)?|跳过|略过|(?:不许|不得|禁止|不能|不可|不要|别)\s*"
    r"(?:执行|运行|跑)|不跑|不执行)|"
    r"(?:测试|单测|测例|用例)\s*(?:一条|一行|一个)?\s*不\s*"
    r"(?:写|加|添|补|创建|新增)\s*[,，、]\s*"
    r"(?:一遍|一次|一回)?\s*不\s*(?:跑|执行|运行)|"
    r"(?P<retest>复测)\s*"
    r"(?:(?:这(?:一)?步|这(?:一)?环节)\s*)?(?:就\s*)?"
    r"(?:不必|无需|无须|不用|不要|不需要)"
    r"(?:再)?(?:做|进行)?",
    re.IGNORECASE,
)
_TEST_ARTIFACT_PRESERVATION_RE = re.compile(
    r"\bavoid\s+(?:creating|adding|writing|changing|modifying)\s+"
    r"(?:(?:any|the)\s+)?tests?\b|"
    r"\bavoid(?:ing)?\s+(?:any\s+)?"
    r"(?:changes?|modifications?|edits?)\s+to\s+"
    r"(?:the\s+)?(?:test\s+files?|test\s+suite|tests?)\b|"
    r"\b(?:leave|leaving)\s+(?:the\s+)?"
    r"(?:test\s+files?|test\s+suite|tests?)\s+"
    r"(?:untouched|unchanged|unmodified)\b|"
    r"\btests?(?:\s+suite)?\b.{0,24}"
    r"\b(?:stay|remain)\s+(?:untouched|unchanged|unmodified)\b|"
    r"测试(?:代码|文件|用例)?[^,，。！？;；\n]{0,12}"
    r"(?:保持|维持)(?:原样|不变)|"
    r"测试(?:代码|文件|用例)?[^,，。！？;；\n]{0,12}"
    r"(?:不许|不得|不能|不要|别|不准|不允许)\s*"
    r"(?:动|改|修改|变更|碰)",
    re.IGNORECASE,
)
_TEST_EXECUTION_ARTIFACT_RE = re.compile(
    r"\btest[-\s]+(?:run|execution)[-\s]+"
    r"(?:reports?|results?|summar(?:y|ies)|docs?|documentation|logs?)\b",
    re.IGNORECASE,
)


def _has_effective_test_denial(pattern: re.Pattern[str], text: str) -> bool:
    """Ignore denial-looking matches inside a positive double negative."""

    positive_spans = tuple(
        match.span() for match in POSITIVE_NEGATION_IDIOM_RE.finditer(text)
    )
    return any(
        not any(
            positive_start <= match.start() < positive_end
            for positive_start, positive_end in positive_spans
        )
        for match in pattern.finditer(text)
    )


def _is_nominal_test_execution_false_positive(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Reject noun-shaped ``test`` hits in creation-only prohibitions."""

    artifact = _TEST_EXECUTION_ARTIFACT_RE.search(clause)
    if (
        artifact is not None
        and artifact.start() <= trigger_span[0] < artifact.end()
    ):
        return True

    if not _has_effective_test_denial(_TEST_CREATION_DENIAL_RE, clause):
        return False
    if _has_effective_test_denial(_TEST_EXECUTION_DENIAL_RE, clause):
        return False
    trigger = clause[trigger_span[0] : trigger_span[1]]
    return re.fullmatch(
        r"(?:tests?|test\s+suite|pytest|测试|单测|测例|用例)",
        trigger,
        re.IGNORECASE,
    ) is not None
_RECOVERY_STATUS_RE = re.compile(
    r"\b(?:now|currently|already)\b[^.!?;\n]{0,32}"
    r"\b(?:works?|working|passes?|passing|healthy|stable|resolved|recovered|fixed)\b"
    r"(?![./_-][a-z0-9])|"
    r"\b(?:runs?|passes?)\s+(?:normally\s+)?"
    r"(?:alone|locally|in\s+isolation)\b|"
    r"(?:单独|独立|本地|手动)\s*(?:运行|跑)\s*(?:正常|稳定|通过)|"
    r"(?:现在|目前|如今|此时|已经|已|刚刚)[^。！？；\n]{0,24}"
    r"(?:恢复(?:到)?(?:正常|稳定)|(?:运行|工作)\s*(?:正常|稳定)|"
    r"(?:测试|构建)\s*(?:已经|已)?\s*通过|"
    r"问题\s*(?:已经|已)?\s*(?:解决|消失))",
    re.IGNORECASE,
)
_EXECUTION_SUBJECT_BEFORE_STATUS_RE = re.compile(
    r"(?:\b(?:the\s+)?ci\b|\bpytest\b|\btests?\b|测试|单测)\s*$",
    re.IGNORECASE,
)
_EXECUTION_STATUS_AFTER_TRIGGER_RE = re.compile(
    r"^\s*(?:(?:pytest|tests?|test\s+suite)\s+)?"
    r"(?:fails?|failed|errored|crashed|passes?|passed|succeeded)\b"
    r"(?!\s+(?:tests?|test\s+suite)\b)|"
    r"^\s*(?:(?:pytest|测试|单测)\s*)?"
    r"(?:失败|报错|崩溃|通过|成功)(?:了)?",
    re.IGNORECASE,
)
_EXECUTION_OBJECT_THEN_REPORT_RE = re.compile(
    r"^\s*(?:(?:the|all)\s+)?(?:pytest|tests?|test\s+suite)\s+"
    r"(?:fails?|failed|errored|crashed|passes?|passed|succeeded)\b|"
    r"^\s*(?:pytest|测试|单测)\s*"
    r"(?:失败|报错|崩溃|通过|成功)(?:了)?|"
    r"^\s*(?:(?:the|all)\s+)?(?:pytest|tests?|test\s+suite)\b"
    r"[^,，。！？!?;；\n]{0,32}[,，]\s*"
    r"(?:it|they|pytest|the\s+tests?)\s+"
    r"(?:fails?|failed|errored|crashed|passes?|passed|succeeded)\b|"
    r"^\s*(?:pytest|测试|单测)[^,，。！？!?;；\n]{0,24}[,，]\s*"
    r"(?:它|测试|单测)?\s*(?:失败|报错|崩溃|通过|成功)(?:了)?",
    re.IGNORECASE,
)

# Execution status is discourse about an earlier attempt, not permission to
# start a new process.  These whole-turn patterns deliberately end at the
# status token so a later command (``CI run failed; rerun tests``) remains
# independently actionable after clause splitting.
_REPORTED_EXECUTION_DISCOURSE_PATTERNS = (
    re.compile(
        r"\b(?:the\s+)?(?:latest\s+|previous\s+|earlier\s+)?ci\b"
        r"[^.!?;\n]{0,48}\b(?:task|attempt|job|pipeline)?\b"
        r"[^.!?;\n]{0,48}(?:failed\s+while\s+running|running\s+"
        r"(?:the\s+)?(?:unit\s+)?tests?\s+(?:ended|resulted)\s+in\s+"
        r"(?:failure|an?\s+failure))\b[^.!?;\n]{0,32}"
        r"(?:tests?|test\s+suite)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byesterday(?:'s)?\s+(?:build|pipeline|ci)\s+log\s+"
        r"(?:records?|recorded|shows?|showed)\s+(?:an?\s+)?failed\s+"
        r"test\s+run\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:同步一?下\s*[:：]?\s*)?(?:pytest|tests?|测试|单测)\s*"
        r"(?:昨晚|昨夜|上次|上一轮|此前|之前)\s*"
        r"(?:执行|运行|跑)\s*(?:失败|挂住|挂了|超时|崩溃)",
        re.IGNORECASE,
    ),
    re.compile(
        # Strong provenance/history anchors make the whole fragment a status
        # report even when it contains an otherwise imperative-looking verb.
        # Stop at a hard clause boundary so a later ``run now`` stays live.
        r"(?:^|(?<=[.!?;。！？；]))\s*(?:fyi\b|for\s+the\s+record\b|"
        r"yesterday(?:'s)?\b|last\s+night\b|(?:the\s+)?ci\s+history\b|"
        r"(?:the\s+)?(?:build|pipeline)\s+(?:log|history|note)\b)"
        r"[^.!?;。！？；\n]{0,120}"
        r"(?:pytest|tests?|test\s+suite|test\s+run|checks?)\b"
        r"[^.!?;。！？；\n]{0,80}"
        r"(?:did\s+run|didn['’]?t\s+(?:run|execute)|did\s+not\s+"
        r"(?:run|execute)|ran|running|executed?|execution|failed\s+test\s+run|"
        r"crashed|hung|timed?\s*out|was\s+not\s+run|were\s+not\s+run)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|(?<=[。！？；]))\s*(?:FYI\s*[,，—-]?|仅报告状态\s*[:：]?|"
        r"昨晚|昨夜|上一轮|上一次|上次|此前|之前|周报(?:里|中)?|"
        r"(?:测试|构建|流水线)报告|CI\s*(?:历史|记录))"
        r"[^。！？；\n]{0,120}(?:pytest|tests?|checks?|测试|单测|测试任务)"
        r"[^。！？；\n]{0,80}"
        r"(?:执行|运行|跑|启动|挂住|挂了|超时|失败|崩溃|"
        r"没有被运行|没有执行|没有运行|未曾执行|未执行|未运行)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:测试报告|构建记录|流水线记录|周报)"
        r"[^。！？；\n]{0,80}(?:显示|表明|提到|记录)"
        r"[^。！？；\n]{0,64}(?:上一轮|昨晚|昨夜|此前|之前)?"
        r"[^。！？；\n]{0,24}(?:没有|没|未|未曾)"
        r"[^。！？；\n]{0,16}(?:执行|运行|跑)(?:成功|起来)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the\s+)?(?:ci(?:\s+(?:job|pipeline))?|pytest|pyetst|pyest|tests?)\b"
        r"\s+(?:run(?:ning)?|execut(?:e|ed|ing))\b"
        r"\s*(?:[-—,:：，；;/]+\s*)?"
        r"(?:(?:is|was|were|has|have|had)\s+(?:now\s+)?)?"
        r"(?:now\s+)?(?:fails?|failed|failing|faield|fialed|faild|errored|"
        r"crashed|passes?|passed|passing|succeeded|recovered|green|"
        r"completed\s+successfully)\b"
        r"(?!\s+(?:tests?|test\s+suite)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the\s+)?(?:ci(?:\s+(?:job|pipeline))?|pytest|pyetst|"
        r"pyest|tests?)\b\s+(?:is|was|were|has\s+been|had\s+been)\s+"
        r"unable\s+to\s+(?:run|execute)\b"
        r"(?:\s+(?:pytest|pyetst|pyest|tests?|test\s+suite))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:tried\s+to|unable\s+to|failed\s+to|impossible\s+to|"
        r"no\s+way\s+to)\s+(?:run|execute)\b"
        r"[^.!?\n]{0,48}(?:pytest|pyetst|tests?|test\s+suite|ci)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:run|execute)\b\s+"
        r"(?:pytest|pyetst|tests?|test\s+suite|单测)\b"
        r"(?!\s+(?:if|when|unless|because|after)\b)"
        r"\s*(?:[-—,:：，；;/]+\s*)?(?:but\s+)?"
        r"(?:(?:it|they|pytest|the\s+tests?)\s+)?"
        r"(?:fails?|failed|failing|faield|faild|errored|crashed|passes?|"
        r"passed|passing|succeeded|recovered|green|"
        r"completed\s+successfully)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:CI\s*)?(?:无法|没能)\s*(?:运行|执行|跑)\s*"
        r"(?:pytest|pyetst|tests?|test|测试|单测)|"
        r"(?:pytest|pyetst|tests?|test|测试|单测)\s*(?:无法|没能)\s*"
        r"(?:运行|执行|跑)|"
        r"(?:运行|执行|跑)不了\s*(?:pytest|pyetst|tests?|test|测试|单测)|"
        r"(?:CI\s*)?没\s*跑起来",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:CI\s*)?(?:运行|执行|跑|run|execute)\s*"
        r"(?:pytest|pyetst|tests?|test|测试|单测)"
        r"\s*(?:[-—,:：，；;/]+\s*)?(?:它\s*)?"
        r"(?:失败|失敗|报错|崩溃|挂了|通过|成功|"
        r"fails?|failed|faield|fialed|faild|errored|crashed|passes?|passed)"
        r"(?![a-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:CI\s*)?(?:任务|作业|流水线|job|pipeline)\s*"
        r"(?:运行|执行|跑|run|execute)\s*"
        r"(?:失败|失敗|报错|崩溃|挂了|通过|成功|"
        r"fails?|failed|faield|fialed|faild|errored|crashed|passes?|passed)|"
        r"(?:pytest|pyetst|pyest|tests?|test|测试|单测)\s*"
        r"(?:(?:没有|没)\s*(?:跑通|运行成功|执行成功)|跑挂了|挂了)|"
        r"(?:运行|执行|跑)\s*(?:pytest|pyetst|pyest|tests?|test|测试|单测)"
        r"\s*(?:挂了|失败|失敗|报错|崩溃|fails?|failed|faield|fialed|faild)",
        re.IGNORECASE,
    ),
    re.compile(
        # A history/report anchor plus an execution frame is status prose.
        # Requiring both signals deliberately leaves the imperative-looking
        # ``CI run failed tests`` available as a current command.
        r"(?:\b(?:yesterday(?:'s)?|previous|latest|earlier|our)\b|"
        r"^\s*fyi\b)"
        r"[^.!?;\n]{0,56}\b(?:ci|pipeline|runner|pytest|tests?)\b"
        r"[^.!?;\n]{0,40}\b(?:run|ran|running|executed?|execution)\b"
        r"[^.!?;\n]{0,40}\b(?:keeps?\s+)?"
        r"(?:fails?|failed|failing|failure|errored|crashed)\b|"
        r"\b(?:the\s+)?ci\s+(?:job|pipeline)\s+could\s+not\s+"
        r"(?:run|execute)\s+(?:pytest|tests?|test\s+suite)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:昨晚|刚才|那次|上次|此前|之前|流水线|CI\s*上的)"
        r"[^,，。！？!?;；\n]{0,48}"
        r"(?:run|execute|运行|执行|跑)"
        r"[^,，。！？!?;；\n]{0,32}"
        r"(?:失败|没成功|未成功|挂掉|挂了|报错|崩溃|"
        r"fails?|failed|failing)|"
        r"(?:pytest|tests?|test|测试|单测)\s*"
        r"(?:run|execution|运行|执行|跑)\s*"
        r"(?:结果|状态)\s*[:：是]?\s*"
        r"(?:失败|没成功|未成功|挂掉|挂了|报错|崩溃|failed|failure)|"
        r"(?:pytest|tests?|test|测试|单测)\s*"
        r"(?:没有|没|未)\s*(?:真正)?\s*(?:运行|执行|跑)起来",
        re.IGNORECASE,
    ),
    re.compile(
        # A past-time anchor is required so a current ``start the tests``
        # command is not confused with a report about a failed launch.
        r"\b(?:yesterday(?:'s)?|last\s+(?:night|time|run)|previous|earlier)\b"
        r"[^.!?;\n]{0,64}\b(?:ci|pipeline|runner|pytest|tests?|test\s+suite)\b"
        r"[^.!?;\n]{0,40}\b(?:could\s+not|did\s+not|never|failed\s+to)\s+"
        r"(?:start|launch|run|execute|get\s+running)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:刚刚那轮|刚才那轮|昨夜(?:的)?|昨晚(?:的)?|上一次|上一轮|"
        r"那一轮|此前|之前)"
        r"[^,，。！？!?;；\n]{0,56}(?:CI|pytest|测试|单测)"
        r"[^,，。！？!?;；\n]{0,40}"
        r"(?:跑崩(?:了)?|崩(?:掉|溃|了)|失败|未成功|没成功|报错|挂了)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:测试|单测|pytest)\s*(?:执行|运行|跑)?\s*"
        r"(?:记录|结果|状态)\s*(?:显示|表明|为|是)\s*"
        r"(?:失败|未成功|没成功|未运行|没运行)|"
        r"(?:日志|构建记录|执行记录|测试记录|流水线记录)"
        r"[^,，。！？!?;；\n]{0,24}(?:显示|表明|记录(?:着)?)"
        r"[^,，。！？!?;；\n]{0,32}(?:pytest|测试|单测)"
        r"[^,，。！？!?;；\n]{0,24}"
        r"(?:根本)?(?:没有|没|未)\s*(?:真正)?\s*(?:执行|运行|跑)",
        re.IGNORECASE,
    ),
)
_EXPLICIT_ACTION_REQUEST_PREFIX_RE = re.compile(
    r"(?:\b(?:please|kindly)\b|"
    r"(?:请(?:你)?|帮我|麻烦(?:你)?|务必|先|再|接着|然后))\s*$",
    re.IGNORECASE,
)
_OBJECT_FRONTED_ACTION_REQUEST_RE = re.compile(
    r"^\s*(?:(?:请(?:你)?|帮我|麻烦(?:你)?)\s*)?(?:把|将)"
    r"[^。！？；,.!?;\n]{0,100}$",
    re.IGNORECASE,
)
_COMPLETED_ACTION_PREFIX_RE = re.compile(
    r"(?:\b(?:already|previously|earlier|yesterday)\b|"
    r"(?:已经|已|刚刚|刚才|此前|之前|昨天|上次))"
    r"\s*(?:(?:顺利|成功|彻底|全部|完全)\s*)?$",
    re.IGNORECASE,
)
_COMPLETED_ACTION_SUFFIX_RE = re.compile(
    r"^\s*(?:了|啦)[。.!！]*\s*$|"
    r"(?:\b(?:was|were|is|are|has|have|had)\s+(?:already\s+)?"
    r"(?:completed|done|fixed|resolved)\b|"
    r"(?:已经|已)\s*(?:顺利|成功|彻底|全部|完全)?\s*"
    r"(?:完成|结束|做完|修好|解决)(?:了|啦|完毕)?|"
    r"(?:完成|结束|做完)(?:了|啦|完毕)?)"
    r"[。.!！]*\s*$",
    re.IGNORECASE,
)
_OPERATION_TOPIC_FILLER_RE = re.compile(
    r"\b(?:please|kindly|just|again|now|then|time|it|this|that|them|those|same|above|"
    r"previous|the|an?|issue|problem|thing|one|ones|cause|reason|plan|"
    r"log|entry|sample|snippet|message|report|trace|line|string|excerpt|"
    r"ticket|note|sentence|example|quoted|text)\b|"
    r"\b(?:based|according|on|from|using|because|per|to)\b|"
    r"(?:这个文件|该文件|不需要|不要|不用|不必|无需|无须|一下吧|这个|这段|"
    r"它|刚才|结论|发现|上面|前述|别|勿|不得|日志|示例|文字|文本|句子|引用|"
    r"请|你|我|只|先|再|也|一起|一下|按|的|方案|问题|原因|结果|掉|好|吧|呢|了|调优)",
    re.IGNORECASE,
)
_OPERATION_SOURCE_ATTRIBUTION_RE = re.compile(
    r"\b(?:based|according)\s+(?:on\s+)?(?:the\s+|this\s+|an?\s+)?"
    r"(?:quoted\s+)?(?:log|entry|record|annotation|evidence|sample|snippet|"
    r"message|report|trace|line|string|excerpt|ticket|note|sentence|example|"
    r"text|console\s+output|incident\s+summary|diagnostic\s+message)"
    r"(?:\s+(?:entry|record|annotation|evidence|sample|snippet|message|report|"
    r"trace|line|string|excerpt|text|output|summary))?\b|"
    r"\b(?:using|from|per)\s+(?:the\s+|this\s+|an?\s+)?(?:quoted\s+)?"
    r"(?:log|entry|record|annotation|evidence|sample|snippet|message|report|"
    r"trace|line|string|excerpt|ticket|note|sentence|example|text|"
    r"console\s+output|incident\s+summary|diagnostic\s+message)"
    r"(?:\s+(?:entry|record|annotation|evidence|sample|snippet|message|report|"
    r"trace|line|string|excerpt|text|output|summary))?\b|"
    r"(?:根据|按照|基于|来自|使用)(?:这个|这条|该|上述|前述)?"
    r"(?:日志|记录|样例|示例|片段|消息|报告|堆栈|行|字符串|工单|备注|"
    r"文本|句子|引用)(?:中的|里的|内容)?",
    re.IGNORECASE,
)
_ANAPHORIC_OPERATION_TOPIC_RE = re.compile(
    r"\b(?:the\s+)?(?:bottleneck|finding|result|issue|problem)"
    r"(?:\s+(?:we|you)\s+(?:found|identified|located|discussed))?\b|"
    r"\b(?:we|you)\s+(?:found|identified|located|discussed)\b|"
    r"(?:刚才|之前|上面|前面)?(?:定位|找到|发现|讨论)(?:到|的)?"
    r"(?:瓶颈|问题|结果|发现)|"
    r"(?:还是|就)(?=\s|[,，。！？!?;；]|$)|"
    r"(?:这个|那个|该)?(?:瓶颈|结果)",
    re.IGNORECASE,
)

_HYPOTHETICAL_RENAME_PREFIX_RE = re.compile(
    r"(?:^|[，,;；。.!！?？])\s*(?:如果|假如|假设|若|要是)"
    r"[^。！？!?;；\n]*$|"
    r"(?:^|[.!?;,]\s*)\s*(?:what\s+if|suppose|assuming|"
    r"should\s+we|could\s+we|would\s+we)\b[^.!?;\n]*$",
    re.IGNORECASE,
)
_RENAME_EFFECT_QUESTION_RE = re.compile(
    r"(?:会|可能会|将会).{0,32}(?:怎样|如何|怎么样|什么|哪些|影响|后果|风险)|"
    r"(?:的)?(?:影响|后果|风险|效果|步骤|含义|意思)"
    r"(?:是什么|有哪些|如何|怎样|怎么样|呢)?|"
    r"\b(?:would|could|might)\b.{0,32}"
    r"\b(?:happen|impact|affect|effect|risk|break)\b|"
    r"\b(?:impact|effect|consequence|risk)s?\b|"
    r"\b(?:means?|meaning)\b",
    re.IGNORECASE,
)
_DISCUSSED_ACTION_PREFIX_RE = re.compile(
    r"(?:解释|说明|分析|检查|评估|讨论|审查|审核|评审|审计|指出|列出|"
    r"告诉我|讲讲|讲解|只讲(?:一?下)?|"
    r"(?:我)?(?:只是|只)?想知道)"
    r"[^。！？!?;；\n]{0,80}$|"
    r"\b(?:explain|describe|analy[sz]e|inspect|assess|discuss|tell\s+me|"
    r"show\s+me|an?\s+explanation\s+of|compare\s+(?:the\s+)?ways?\s+to)\b"
    r"[^.!?;\n]{0,80}$|"
    r"^\s*why\s+would\s+(?:someone|anyone|we|you)\s+[^.!?;\n]{0,40}$",
    re.IGNORECASE,
)
_EXPLANATION_COMPLEMENT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:我)\s*)?(?:只是|只|仅)?\s*"
    r"(?:解释|说明|讲(?:讲|解)?|想知道)"
    r"(?:一?下)?(?:而已)?\s*[:：]?\s*"
    r"(?:为什么|为何|怎么|怎样|如何|含义|意思|步骤|影响)"
    r"[^,，。！？!?;；\n]{0,24}"
    r"(?:要|会|该|应该|需要|可以|能)?\s*$|"
    r"^\s*(?:please\s+)?(?:only|just)?\s*(?:explain|describe|show\s+me)\b"
    r"[^.!?;\n]{0,32}\b(?:why|how|meaning|steps?|impact)\b"
    r"[^.!?;\n]{0,20}$|"
    r"^\s*why\s+would\s+(?:someone|anyone|we|you)\s+[^.!?;\n]{0,24}$|"
    r"^\s*(?:could\s+you\s+)?compare\s+(?:the\s+)?ways?\s+to\s*$|"
    r"^\s*(?:我)?(?:只|仅)?\s*讲(?:一?下)?\s*如果\s*(?:要|需要)?\s*$|"
    r"^\s*(?:i\s+want\s+)?an?\s+explanation\s+of\s*$",
    re.IGNORECASE,
)
_EXPLANATION_AUTHORITY_RESET_RE = re.compile(
    r"\b(?:then|now|instead|immediately)\b|"
    r"(?:然后|再|现在|而是|马上|立即)",
    re.IGNORECASE,
)
_EXPLANATION_EMBEDDED_QUESTION_RE = re.compile(
    r"(?:为什么|为何|怎样|如何|哪些|什么|是否)|"
    r"\b(?:why|how|what|which|whether)\b",
    re.IGNORECASE,
)
_EXPLANATION_DESCRIBED_SUBJECT_RE = re.compile(
    r"(?:它|这(?:条|段|个|项)|该)(?:命令|代码|脚本|建议|方案|操作|功能)?"
    r"[^,，。！？;；\n]{0,24}(?:想|希望|打算|试图|旨在|要|会)\s*$|"
    r"\b(?:this|that|the)\s+(?:command|code|script|suggestion|operation)\b"
    r"[^.!?;\n]{0,32}\b(?:to|would|will)\s*$",
    re.IGNORECASE,
)
_POSTPOSED_SOURCE_ADVICE_RE = re.compile(
    r"^\s*(?:日志|控制台|报告|输出|文档)(?:里|中)(?:的)?\s+"
    r"(?:只是|仅是|是)\s*(?:应用|程序|系统|工具|作者)"
    r"(?:打印出|输出|给出|提出)(?:来)?的\s*(?:建议|提示|说明)"
    r"[。.!！]?\s*$",
    re.IGNORECASE,
)
_EXECUTION_CIRCUMSTANCE_SUFFIX_RE = re.compile(
    r"^\s*[^,，。！？;；\n]{1,96}?(?:的时候|期间|时)\s*[,，]?"
    r"(?P<result>[^。！？;；\n]{0,120})",
    re.IGNORECASE,
)
_EXECUTION_CIRCUMSTANCE_RESULT_RE = re.compile(
    r"(?:导致|出现|发生|取不到|无法|不能|返回了)|"
    r"(?:报错|失败|崩溃|卡住)(?:了|啦)?(?=$|[\s,，。.!！]|的?原因)|"
    r"\b(?:fails?|failed|crashes?|crashed|raises?|raised|returns?|returned)\b|"
    r"\b(?:is|are|was|were)\s+missing\b",
    re.IGNORECASE,
)
_EXECUTION_CIRCUMSTANCE_PREFIX_RE = re.compile(
    r"^\s*(?:when|while)\s*$", re.IGNORECASE
)
_EXECUTION_CONDITIONAL_OR_DIRECTIVE_RE = re.compile(
    # A condition or prescribed consequence does not assert an observed
    # result. Recognize its grammar independently of the handler's vocabulary.
    r"(?:如果|一旦|倘若|若是|若|就|则|那么|应当|应该|需要|必须|请)|"
    r"(?<!需)要(?!求)|\b(?:if|unless|whenever|then|must|should|please)\b",
    re.IGNORECASE,
)
_EXECUTION_CONSEQUENCE_ADVERB_RE = re.compile(
    r"(?:立即|立刻|马上)|\bimmediately\b", re.IGNORECASE
)
_GENERIC_CAUSE_DIAGNOSIS_RE = re.compile(
    r"\b(?:diagnose|investigate)\s+(?:the\s+)?(?:root\s+)?cause\b|"
    r"(?:分析|诊断|排查)[^,，。！？;；\n]{0,48}"
    r"(?:错误|故障|失败|异常|报错|缓存|命中率|成功率|延迟|耗时|吞吐量)"
    r"[^,，。！？;；\n]{0,48}(?:原因|根因)",
    re.IGNORECASE,
)
_CAUSAL_SOFTWARE_SUBJECT_RE = re.compile(
    r"(?:缓存|源码|(?:现有|当前|已有)实现)|\b(?:cache|source\s+code)\b",
    re.IGNORECASE,
)
_INSTRUCTIONAL_EXECUTION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:请|麻烦)?\s*"
    r"(?:说说|讲讲|解释|说明|告诉我|教我|请问|想知道)"
    r"[^。！？!?;；\n]{0,32})?"
    r"(?:(?:我|我们|咱们)\s*)?(?:该\s*)?"
    r"(?:怎么|怎样|如何)[^。！？!?;；\n]{0,32}\s*$|"
    r"^\s*(?:please\s+)?"
    r"how\s+(?:do|can|should|would)\s+(?:i|we|you)"
    r"[^.!?;\n]{0,24}\s*$|"
    r"^\s*(?:(?:could|can|would)\s+you\s+)?(?:please\s+)?"
    r"(?:explain|describe|tell\s+me)\b[^.!?;\n]{0,32}"
    r"\bhow\s+(?:to|(?:i|we|you)\s+(?:can|should|would))"
    r"[^.!?;\n]{0,24}\s*$",
    re.IGNORECASE,
)
_DENIED_PROHIBITION_MENTION_RE = re.compile(
    r"(?:不是|并非)(?:让|叫|要)(?:你)?\s*"
    r"(?:不要|别|不用|不必|无需|无须|甭)\s*"
    r"(?:查|查找|搜索|检查|审(?:查|核|计)?|修|修改|更新|删|删除|跑|运行|执行|"
    r"重命名|改名)|"
    r"\b(?:not\s+(?:asking|telling)\s+you\s+not\s+to)\s+"
    r"(?:search|check|review|fix|update|delete|run|execute|rename)\b",
    re.IGNORECASE,
)
_DISCURSIVE_DOUBLE_NEGATION_PREFIX_RE = re.compile(
    r"(?:不用|不需要|不是|并非|并不是)\s*不\s*"
    r"(?:(?:让|叫|要求)(?:你)?\s*)?$",
    re.IGNORECASE,
)
_NOMINAL_SETTING_SUFFIX_RE = re.compile(
    r"^\s*(?:文件|项|参数|目录|路径|值)(?:\b|[\u4e00-\u9fff])",
    re.IGNORECASE,
)
_ACCIDENTAL_ACTION_PREFIX_RE = re.compile(
    r"(?:误|错|意外|不小心)\s*$|\b(?:accidentally|inadvertently)\s*$",
    re.IGNORECASE,
)
_NOMINAL_HANDLER_PREFIX_RE = re.compile(r"(?:异常|错误|报错)\s*$")
_NOMINAL_HANDLER_SUFFIX_RE = re.compile(
    r"^\s*(?:逻辑|流程|机制|路径|代码|有没有|是否|有无|的)",
)
_NOMINAL_MEMORY_QUERY_SUFFIX_RE = re.compile(
    r"^[^,，。！？;；\n]{0,48}的(?:代码|函数|方法|功能|实现|记录|信息)"
    r"[^,，。！？;；\n]{0,24}(?:在哪|哪里|什么|哪些|怎么|如何|是否|有没有)",
)
_READ_NOMINAL_GOVERNOR_RE = re.compile(
    r"(?:找出|查找|搜索|查看|阅读|解释|说明|分析|检查|审查|审核|评审|评估|"
    r"比较|对比|列出|展示)|"
    r"\b(?:find|search|locate|read|inspect|review|explain|describe|analy[sz]e|"
    r"compare|show)\b",
    re.IGNORECASE,
)
_NOMINAL_OPERATION_HEAD_RE = re.compile(
    r"^\s*(?:函数|方法|模块|脚本|逻辑|流程|机制|功能|接口|步骤|命令|语句|策略)|"
    r"^[^,，。！？;；\n]{0,48}的(?:代码|函数|方法|模块|脚本|逻辑|流程|机制|"
    r"功能|接口|步骤|命令|语句|策略)|"
    r"^\s+(?:function|method|module|script|logic|workflow|mechanism|feature|"
    r"interface|command|statement|strategy)\b",
    re.IGNORECASE,
)
_NOMINAL_GOVERNOR_RESET_RE = re.compile(
    r"[,，。！？!?;；\n]|(?:然后|接着|随后|之后|以后|再|并且|并|同时)|"
    r"后\s*(?:(?:请|帮我|直接|立即|立刻|马上)\s*)*$|"
    r"^完(?:毕)?[^,，。！？!?;；\n]{0,48}(?:直接|立即|立刻|马上)\s*$|"
    r"\b(?:then|next|afterwards|and|now)\b",
    re.IGNORECASE,
)
_DOCUMENT_ACTION_TARGET_RE = re.compile(
    r"^\s*(?:一?下)?\s*(?:readme(?:\.[a-z]+)?\b|docs?\b|"
    r"documentation\b|文档|说明|指南|手册|注释|docstring\b)",
    re.IGNORECASE,
)
_DOCUMENT_CONTENT_CONTINUATION_RE = re.compile(
    r"^\s*(?P<action>补上|补充|添加|写上|加上)"
    r"[^。！？;；\n]{0,100}(?:步骤|说明|用法|示例|章节|段落|注意事项)"
    r"[,，。.!！]?\s*$",
    re.IGNORECASE,
)


def _is_nominal_effectful_action(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Keep noun compounds and accidental events out of the action lane.

    This inspects the role of the matched verb, so ``处理异常`` and
    ``删除误写的文件`` retain their direct action evidence.
    """

    start, end = trigger_span
    trigger = clause[start:end]
    before, after = clause[:start], clause[end:]
    governor = tuple(_READ_NOMINAL_GOVERNOR_RE.finditer(before))
    governed_noun = bool(
        governor
        and _NOMINAL_GOVERNOR_RESET_RE.search(before[governor[-1].end() :])
        is None
        and _NOMINAL_OPERATION_HEAD_RE.search(after)
    )
    return bool(
        # In ``找出编号的生成函数`` / ``查看删除文件的代码``, the read
        # verb owns the noun phrase. Its embedded verb is not another task.
        # An explicit sequence reset leaves the later command independent.
        governed_noun
        or (trigger in {"配置", "设置"} and _NOMINAL_SETTING_SUFFIX_RE.search(after))
        or (
            trigger in {"记住", "记下"}
            and _NOMINAL_MEMORY_QUERY_SUFFIX_RE.search(after)
        )
        or _ACCIDENTAL_ACTION_PREFIX_RE.search(before)
        or (
            trigger == "处理"
            and _NOMINAL_HANDLER_PREFIX_RE.search(before)
            and (
                _NOMINAL_HANDLER_SUFFIX_RE.search(after)
                or (
                    _DISCUSSED_ACTION_PREFIX_RE.search(before)
                    and re.fullmatch(r"[\s。.!！?？]*", after)
                )
            )
        )
    )


def _is_denied_prohibition_mention(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Treat a denial of a prohibition as discourse, not a second action."""

    start, end = trigger_span
    return any(
        match.start() <= start < end <= match.end()
        for match in _DENIED_PROHIBITION_MENTION_RE.finditer(clause)
    )


def _is_discursive_double_negation_mention(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Do not turn a descriptive double negative into task authority."""

    start, _end = trigger_span
    return (
        _DISCURSIVE_DOUBLE_NEGATION_PREFIX_RE.search(clause[:start])
        is not None
    )


def _is_discussed_rename(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Reject rename words used as hypothetical or review subjects."""

    start, end = trigger_span
    if is_span_negated(clause, trigger_span):
        return False
    before = clause[:start]
    after = clause[end:]
    if _HYPOTHETICAL_RENAME_PREFIX_RE.search(before):
        return True
    return bool(
        _is_discussed_effectful_action(clause, trigger_span)
        or (
            _RENAME_EFFECT_QUESTION_RE.search(after)
            and re.search(r"[?？]\s*$", clause)
        )
    )


def _is_discussed_effectful_action(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Return whether an effectful verb is only an explanation subject.

    The decision requires an explanation governor plus either a how/why
    complement before the verb or an effect/meaning suffix after it.  A
    bounded authority reset keeps ``explain it, then run pytest`` actionable.
    """

    start, end = trigger_span
    if is_span_negated(clause, trigger_span):
        return False
    before = clause[:start]
    after = clause[end:]
    if _is_nominal_effectful_action(clause, trigger_span):
        return True
    hypothetical = _HYPOTHETICAL_RENAME_PREFIX_RE.search(before)
    if hypothetical is not None and re.search(
        r"(?:[,，]\s*(?:现在|当前|实际|直接|立即|马上)\s*|"
        r"(?:然后|接着|随后))[^。！？;；\n]{0,48}$",
        before[hypothetical.start() :],
    ) is None:
        # A supposed event is a subject for reasoning. A conditional command
        # such as ``如果测试通过就删除临时文件`` still needs its real action.
        conditional_command = re.search(
            r"(?:就|则|便|那么|那就)\s*(?:(?:请|把|将)\s*)?"
            r"[^。！？;；\n]{0,48}$",
            before[hypothetical.start() :],
        )
        if conditional_command is None and (
            re.search(
                r"(?:假设|假如|有人|某人)|\b(?:what\s+if|suppose|assuming)\b",
                hypothetical.group(0),
                re.IGNORECASE,
            )
            or _RENAME_EFFECT_QUESTION_RE.search(after)
        ):
            return True
    governor = _DISCUSSED_ACTION_PREFIX_RE.search(before)
    if governor is not None and _EXPLANATION_AUTHORITY_RESET_RE.search(
        before[governor.start() :]
    ):
        return False
    if _EXPLANATION_COMPLEMENT_PREFIX_RE.fullmatch(before):
        return True
    if governor is not None and _EXPLANATION_EMBEDDED_QUESTION_RE.search(
        clause[governor.start() :]
    ):
        # ``explain what the command fixes`` and ``说明它想解决什么``
        # embed an action below an explanatory question. The outer request
        # grants reading authority; its subject's verb grants none.
        if _EXPLANATION_EMBEDDED_QUESTION_RE.search(before) or (
            _EXPLANATION_DESCRIBED_SUBJECT_RE.search(before)
        ):
            return True
    return bool(
        governor is not None
        and _RENAME_EFFECT_QUESTION_RE.search(after)
    )


def _is_discussed_mutation(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Reject a mutation verb governed by an explanation of its effects."""

    return _is_discussed_effectful_action(clause, trigger_span)


def _is_instructional_execution_mention(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Keep a mentioned command in an explanation lane, not execution."""

    start, _end = trigger_span
    return bool(
        _INSTRUCTIONAL_EXECUTION_PREFIX_RE.search(clause[:start])
        or _is_discussed_effectful_action(clause, trigger_span)
    )


def _is_reported_execution_mention(
    clause: str,
    trigger_span: tuple[int, int],
    *,
    following_text: str = "",
) -> bool:
    """Reject status prose that happens to contain an execution verb."""

    start, end = trigger_span
    before = clause[:start]
    after = clause[end:]
    # A comma before a handler can be split into another parser clause. Keep
    # that immediate continuation in the execution frame; a new sentence has
    # independent authority and cannot turn a report back into a command.
    continuation = re.split(
        r"[。！？;；\n]|\.(?=\s|$)", following_text, maxsplit=1
    )[0]
    if re.match(
        r"^[,，]\s*(?:(?:请|麻烦|帮我|please)\s*)?"
        + _CONTEXT_SAFE_PROBE_FRAGMENT,
        continuation,
        re.IGNORECASE,
    ):
        # Asking to inspect a reported outcome is a separate read-only task,
        # not an instruction for how to handle a future process failure.
        continuation = ""
    circumstance = _EXECUTION_CIRCUMSTANCE_SUFFIX_RE.match(after)
    result = (
        circumstance.group("result")
        if circumstance is not None
        else after.split(",", 1)[-1]
        if _EXECUTION_CIRCUMSTANCE_PREFIX_RE.fullmatch(before)
        else ""
    ).lstrip()
    fact = _EXECUTION_CIRCUMSTANCE_RESULT_RE.search(result)
    leading_action = ACTION_MARKER_RE.match(result)
    if (
        fact is not None
        and not is_span_negated(clause, trigger_span)
        and not _EXPLICIT_ACTION_REQUEST_PREFIX_RE.search(before)
        and not _EXECUTION_CONDITIONAL_OR_DIRECTIVE_RE.search(
            before + result + continuation
        )
        and not _EXECUTION_CONSEQUENCE_ADVERB_RE.search(result[fact.end() :])
        and not (leading_action is not None and leading_action.start() < fact.start())
    ):
        # A factual predicate is positive evidence of background discourse.
        # A leading task verb such as ``记录所有失败请求`` governs its failure
        # noun; a conditional such as ``如果失败立即退出`` asserts no result.
        return True
    return bool(
        (
            _EXECUTION_SUBJECT_BEFORE_STATUS_RE.search(before)
            and _EXECUTION_STATUS_AFTER_TRIGGER_RE.search(after)
        )
        or _EXECUTION_OBJECT_THEN_REPORT_RE.search(after)
    )


def _reported_execution_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return whole-turn spans that describe closed execution attempts."""

    spans = sorted(
        {
            match.span()
            for pattern in _REPORTED_EXECUTION_DISCOURSE_PATTERNS
            for match in pattern.finditer(text)
        }
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _operation_trigger_is_reported_execution(
    trigger_span: tuple[int, int],
    clause_span: tuple[int, int],
    reported_spans: tuple[tuple[int, int], ...],
) -> bool:
    absolute = (
        clause_span[0] + trigger_span[0],
        clause_span[0] + trigger_span[1],
    )
    return any(
        report_start < absolute[1] and absolute[0] < report_end
        for report_start, report_end in reported_spans
    )


_CHINESE_VIEW_SWITCH_FROM_CHANGE_RE = re.compile(
    rf"改{_VIEW_SWITCH_AFTER_CHANGE_PATTERN}",
    re.IGNORECASE,
)
_ENGLISH_VIEW_SWITCH_FROM_CHANGE_RE = re.compile(
    rf"{_ENGLISH_VIEW_SWITCH_PREFIX_PATTERN}\s+"
    r"(?:(?:(?:only|just)\s+)?(?:inspect|review|check|read|analy[sz]e|"
    r"look\s+at)\b\s+)?"
    r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
    r"json|yaml|yml|toml)\b",
    re.IGNORECASE,
)
_VIEW_SWITCH_EXCLUDED_FILE_RE = re.compile(
    r"\b(?:rather\s+than|instead\s+of|not)\s+"
    r"(?P<target_after>[\w/\\._-]+\."
    r"(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))\b|"
    r"(?:别|不要)\s*(?:再)?(?:看|查看|检查|审查|读|阅读)\s*"
    r"(?P<target_denied>[\w/\\._-]+\."
    r"(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))\b|"
    r"(?P<target_abandoned>[\w/\\._-]+\."
    r"(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|json|yaml|yml|toml))"
    r"\s*放下",
    re.IGNORECASE,
)


def _is_view_switch_change_mention(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Keep ``改看`` in the read/switch lane rather than the mutation lane."""

    return any(
        pattern.match(clause, trigger_span[0]) is not None
        for pattern in (
            _CHINESE_VIEW_SWITCH_FROM_CHANGE_RE,
            _ENGLISH_VIEW_SWITCH_FROM_CHANGE_RE,
        )
    )


def _is_completed_action_mention(
    clause: str,
    trigger_span: tuple[int, int],
) -> bool:
    """Return whether an action-shaped phrase reports completed work.

    The parser treats mutations as authority only when the current turn asks
    for them.  Temporal/aspect markers around an action instead describe
    historical state.  A request cue immediately governing the action keeps
    imperative forms such as ``请先修复`` affirmative.
    """

    start, end = trigger_span
    before = clause[:start]
    after = clause[end:]
    if is_span_negated(clause, trigger_span):
        return False
    recovery = _RECOVERY_STATUS_RE.search(clause)
    if recovery is not None and recovery.start() <= start < recovery.end():
        return True
    if _COMPLETED_ACTION_PREFIX_RE.search(before):
        return True
    if (
        _EXPLICIT_ACTION_REQUEST_PREFIX_RE.search(before)
        or _OBJECT_FRONTED_ACTION_REQUEST_RE.fullmatch(before)
    ):
        return False
    return bool(_COMPLETED_ACTION_SUFFIX_RE.search(after))


def _has_explicit_operation_topic(operation_text: str) -> bool:
    """Detect a current target without relying on a closed domain lexicon."""

    if _TECHNICAL_MARKER_RE.search(operation_text):
        return True
    # Remove attribution as one phrase before the broad action-marker grammar
    # consumes connectors such as ``using``/``from``.  Otherwise the leftover
    # source noun (``annotation`` or ``incident summary``) looks like a new
    # mutation target and can launder unsafe history.
    remainder = _OPERATION_SOURCE_ATTRIBUTION_RE.sub(" ", operation_text)
    remainder = ACTION_MARKER_RE.sub(" ", remainder)
    remainder = _REFERENCE_RE.sub(" ", remainder)
    remainder = _PRIOR_RESULT_REFERENCE_RE.sub(" ", remainder)
    remainder = _ANAPHORIC_OPERATION_TOPIC_RE.sub(" ", remainder)
    remainder = _OPERATION_TOPIC_FILLER_RE.sub(" ", remainder)
    return bool(
        re.search(
            r"\b[a-z0-9][a-z0-9_./:+-]*\b|[\u4e00-\u9fff]{2,}",
            remainder,
            re.IGNORECASE,
        )
    )


_ROOT_CAUSE_RE = re.compile(
    r"\b(?:root\s+cause|cause|reason|why|clue)\b|"
    r"(?:根因|原因|为什么|为何|为啥|啥原因|咋回事|怎么回事)",
    re.IGNORECASE,
)
_CONCURRENCY_PROBLEM_RE = re.compile(
    r"\b(?:deadlock|race|contention|hang)\b|(?:死锁|竞争|卡死)",
    re.IGNORECASE,
)
_CONCURRENCY_ANALYSIS_RE = re.compile(
    r"\b(?:worker\s+pool|threads?|locks?|concurrency|races?|contention)\b|"
    r"(?:线程|锁|并发|竞争)",
    re.IGNORECASE,
)
_PERFORMANCE_SYMPTOM_RE = re.compile(
    r"\b(?:slow|latency|bottleneck|p\d{2,3}|full[- ]table\s+scan|"
    r"full[- ]scann\w*)\b|"
    r"(?:慢|延迟|耗时|瓶颈|热点|全表扫描|扫全表|扫整表|磨磨唧唧|烧哪)",
    re.IGNORECASE,
)
_TRAILING_PERFORMANCE_LOCATION_RE = re.compile(
    r"^\s*(?:看看|看一?下|查一?下|瞅瞅)?\s*"
    r"(?:慢|延迟|耗时|瓶颈).{0,20}"
    r"(?:在哪|哪里|zai\s*哪|where)\s*$",
    re.IGNORECASE,
)
_TRAILING_EXPLAIN_QUALIFIER_RE = re.compile(
    r"^\s*(?:只|仅)\s*(?:解释|说明)(?:一?下)?\s*$",
    re.IGNORECASE,
)
_VAGUE_ACTION_RULES: tuple[tuple[re.Pattern[str], IntentType, ActionType], ...] = (
    (
        re.compile(
            r"(?<![a-z0-9_])(?:delete|remove)(?![a-z0-9_])|"
            r"(?:删除|移除|删掉|删(?!改))",
            re.IGNORECASE,
        ),
        IntentType.CODE,
        ActionType.DELETE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])rename(?![a-z0-9_])|"
            r"(?:重命名|改名)(?!\s*(?:逻辑|规则|处理|实现|代码))",
            re.IGNORECASE,
        ),
        IntentType.CODE,
        ActionType.RENAME,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:re[- ]?run|run|execute)(?![a-z0-9_])|"
            r"(?:运行|执行|跑(?:下|一下|一遍)?)",
            re.IGNORECASE,
        ),
        IntentType.TEST,
        ActionType.EXECUTE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:review|audit|inspect|check|analy[sz]e)"
            r"(?![a-z0-9_])|\b(?:give\s+me\s+)?an?\s+read[- ]only\s+"
            r"assessment\b|\btake\s+a(?:\s+(?:quick|read[- ]only))*\s+look\b|"
            r"(?:审查|审核|审计|评审|检查|查看|复核|瞅瞅|瞄一眼|过一眼|看看|"
            r"看一?下|只读分析|"
            r"审(?=一?下|就|吧|[,，。！？;；]|$)|"
            r"(?:(?:先\s*)?帮我|只|仅)\s*分析)",
            re.IGNORECASE,
        ),
        IntentType.REVIEW,
        ActionType.ANALYZE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:search|find|locate|lookup)(?![a-z0-9_])|"
            r"(?:搜索|查找|查(?!询)(?:一?下)?|定位|找出|"
            r"找找|找一找|"
            r"(?:只|仅)\s*(?:看|读))",
            re.IGNORECASE,
        ),
        IntentType.SEARCH,
        ActionType.READ,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])deploy(?:ment)?(?![a-z0-9_])|"
            r"(?:部署|发布|上线)",
            re.IGNORECASE,
        ),
        IntentType.CONFIGURE,
        ActionType.UPDATE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:explain|explian|describe)(?![a-z0-9_])|"
            r"(?:解释|说明|分析|捋顺|捋清|捋捋|捋一下|讲讲|说说|说明白)",
            re.IGNORECASE,
        ),
        IntentType.EXPLAIN,
        ActionType.READ,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:write|document)(?![a-z0-9_])|(?:写|编写)",
            re.IGNORECASE,
        ),
        IntentType.DOCUMENT,
        ActionType.CREATE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:fix|repair|solve|resolve)(?![a-z0-9_])|"
            r"(?:修复|解决|修(?:一)?下|修好|修完|修)",
            re.IGNORECASE,
        ),
        IntentType.DEBUG,
        ActionType.UPDATE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:refactor|optimi[sz]e|tune)(?![a-z0-9_])|"
            r"(?:重构|优化|调优)",
            re.IGNORECASE,
        ),
        IntentType.REFACTOR,
        ActionType.UPDATE,
    ),
    (
        re.compile(
            r"(?<![a-z0-9_])(?:modif(?:y|ies|ied|ying)|updat(?:e|es|ed|ing)|"
            r"chang(?:e|es|ed|ing)|edit(?:s|ed|ing)?|touch(?:es|ed|ing)?)"
            r"(?![a-z0-9_])|"
            r"(?:修改|更新|调整|"
            rf"改(?!{_VIEW_SWITCH_AFTER_CHANGE_PATTERN})(?:一?下)?|"
            r"补齐|触碰|碰|"
            r"(?<![启运自活行驱移])动)",
            re.IGNORECASE,
        ),
        IntentType.CODE,
        ActionType.UPDATE,
    ),
)

_EXPLICIT_BARE_READ_ONLY_RE = re.compile(
    r"\s*(?:(?:could|can|would)\s+you\s+)?"
    r"(?:(?:please|only|just|briefly)\s+)*"
    r"(?:review|audit|inspect|check|analy[sz]e|explain|describe|read|"
    r"look(?:\s+at)?|(?:give\s+me\s+)?an?\s+read[- ]only\s+assessment|"
    r"take\s+a(?:\s+(?:quick|read[- ]only))*\s+look)"
    r"(?:\s+(?:it|this|that))?"
    r"(?:\s*,?\s*(?:briefly|nothing\s+(?:else|further|more)|"
    r"(?:only\s+)?for\s+(?:now|the\s+moment)|please|only|"
    r"in\s+(?:one|two|three|\d+)\s+sentences?\s+only|"
    r"and\s+stop\s+there|no\s+changes))*\s*[,，]?\s*[。.!！?？]?\s*|"
    r"\s*(?:(?:目前|现在|暂时|先)\s*)?(?:麻烦|帮我)?\s*(?:简单\s*)?"
    r"(?:只|仅|就)?\s*(?:做\s*)?"
    r"(?:看看|查看|看|读|只读分析|只读|分析|审查|审核|检查|解释|说明|复核|审)"
    r"(?:一?下|一遍|两句)?(?:着)?(?:就好|就行|即可)?"
    r"(?:\s*[,，]\s*(?:先别做别的|不做处理|(?:不要|别)处理))?\s*[,，]?\s*"
    r"[。.!！?？]?\s*",
    re.IGNORECASE,
)

_CONTEXT_REUSE_RE = re.compile(
    r"(?:按这个方案做|照刚才(?:说的)?落地|按刚才(?:的|说的|定位的)?|"
    r"先弄清楚为什么|它具体为什么慢|这个也一起改掉|你修一下吧|"
    r"继续[\s,，、]*(?:只\s*)?(?:查|看|检查|核对|审查|分析)|"
    r"接着(?:查|看|处理))|"
    r"\b(?:apply that plan|do that|continue with it|why is it slow)\b",
    re.IGNORECASE,
)
_CONTEXT_SUFFIX_CONTINUATION_RE = re.compile(
    r"\b(?:keep\s+going|continue|go\s+on)\b",
    re.IGNORECASE,
)

_CONTEXT_DEICTIC_FRAGMENT = (
    r"(?:"
    r"(?:就|还是)?(?:这个文件|那个文件|这份文件|那份文件|该文件|"
    r"它|这个|那个|这(?:一)?(?:块|处|个|点)|"
    r"那(?:一)?(?:块|处|个|点)|这个毛病|这个问题)|"
    r"(?:按|照)(?:上面|前面|之前)(?:说的)?|"
    r"(?:(?:still|same)\s+)?(?:this|that)\s+(?:file|module|function)|"
    r"(?:(?:still|还是)\s+)?(?:this|that)\s+one|"
    r"same\s+(?:thing|one|spot|area|issue)|do\s+that|"
    r"(?:it|this|that)"
    r")"
)
_CONTEXT_SAFE_PROBE_FRAGMENT = (
    r"(?:排查|定位|调查|查(?:下去)?|看|检查|核对|审(?:查)?|评审|复核|分析|"
    r"捋(?:捋)?|过(?:一眼)?|挖(?:挖)?|刨|筛|量|测量|"
    r"look(?:ing)?|inspect(?:ing)?|review(?:ing)?|analy[sz](?:e|ing)|"
    r"search(?:ing)?|explain(?:ing)?|"
    r"investigat(?:e|ing)|profil(?:e|ing)|dig(?:ging)?|measure|check(?:ing)?)"
)
_CONTEXT_PROGRESS_PROBE_FRAGMENT = (
    r"(?:"
    r"(?:(?:继续|接着)\s*(?:往(?:下|深里)\s*)?|"
    r"往下|再(?:往(?:下|深里))?)\s*[,，、—-]?\s*"
    r"(?:(?:只|仅)\s*)?(?:仔细\s*)?"
    + _CONTEXT_SAFE_PROBE_FRAGMENT
    + r"(?:下去|一层|一轮|一遍)?|"
    r"(?:continue|go\s+on)\s+(?:to\s+)?"
    + _CONTEXT_SAFE_PROBE_FRAGMENT
    + r"|keep\s+(?:on\s+)?(?:digging|looking|investigating|profiling)|"
    r"(?:investigate\s+further|dig\s+deeper)"
    r")"
)
_CONTEXT_REASON_PROBE_RE = re.compile(
    r"^\s*(?:先\s*)?(?:弄清楚|搞清楚|查清楚)\s*(?:它|这个|那个)?"
    r"(?:具体)?\s*(?:为什么|为何|为啥)(?:慢)?\s*[?？。.]?\s*$|"
    r"^\s*(?:它|这个|那个)\s*(?:具体)?\s*"
    r"(?:为什么|为何|为啥)\s*(?:慢|卡|失败)?\s*[?？。.]?\s*$|"
    r"^\s*(?:first\s+)?(?:find|work|figure)\s+out\s+why"
    r"(?:\s+(?:it|this|that)\s+(?:is\s+)?(?:slow|failing))?"
    r"\s*[?？.]?\s*$",
    re.IGNORECASE,
)
_CONTEXT_COMPOSITIONAL_READ_ONLY_RE = re.compile(
    r"(?:^|(?<=[,，;；。.!?？]))\s*(?:"
    + _CONTEXT_DEICTIC_FRAGMENT
    + r"\s*[,，、—-]?\s*(?:为啥不是每次都出[?？]?\s*)?"
    + _CONTEXT_PROGRESS_PROBE_FRAGMENT
    + r"|"
    + _CONTEXT_PROGRESS_PROBE_FRAGMENT
    + r"\s*[,，、—-]?\s*"
    + _CONTEXT_DEICTIC_FRAGMENT
    + r"|"
    + _CONTEXT_SAFE_PROBE_FRAGMENT
    + r"\s*(?:for\s+)?[,，、—-]?\s*"
    + _CONTEXT_DEICTIC_FRAGMENT
    + r"|"
    + _CONTEXT_PROGRESS_PROBE_FRAGMENT
    + r"|keep\s+(?:that|this)\s+(?:review|investigation|analysis)\s+going|"
    r"(?:还是)?(?:它|这个|那个)\s*[,，、—-]?\s*"
    r"优缺点\s*再\s*捋(?:捋)?"
    r")",
    re.IGNORECASE,
)
_CONTEXT_EXPLICIT_TARGET_SWITCH_RE = re.compile(
    r"(?:换到|切到|转到|改看|先别管|另一个(?:任务|目标|问题|文件)|"
    r"不是(?:这个|那个))|"
    r"\b(?:not\s+that|switch\s+to|skip\s+(?:it|that|the\s+previous)|"
    r"(?:different|new)\s+(?:task|issue|target|file))\b",
    re.IGNORECASE,
)
_CONTEXT_FILE_TARGET_RE = FILE_TOKEN_RE


def _isolated_protected_file_targets(text: str) -> tuple[str, ...]:
    """Return quoted file tokens, excluding prose/examples that mention files."""

    targets: list[str] = []
    quote_characters = "`\"'“”‘’ \t\r\n"
    for start, end in _protected_intent_literal_spans(text):
        candidate = text[start:end].strip(quote_characters)
        matches = tuple(_CONTEXT_FILE_TARGET_RE.finditer(candidate))
        if not matches:
            continue
        residual = list(candidate)
        for match in matches:
            for index in range(match.start(), match.end()):
                residual[index] = " "
        residual_text = re.sub(
            r"\b(?:and|or)\b|(?:和|与|、)|[+,，;；]",
            " ",
            "".join(residual),
            flags=re.IGNORECASE,
        )
        if _has_meaningful_context_residual(residual_text):
            continue
        for match in matches:
            if match.group(1) not in targets:
                targets.append(match.group(1))
    return tuple(targets)


def _read_only_protected_file_targets(text: str) -> tuple[str, ...]:
    """Return file tokens inside literals that are themselves being read.

    A mutation request must never obtain a target from quoted/code text.  A
    read-only request such as ``describe `update quota.py``, however, is
    explicitly about that literal and should retain its file as the object of
    the explanation.  The caller applies this helper only to read-only lanes.
    """

    return tuple(
        dict.fromkeys(
            match.group(1)
            for start, end in _protected_intent_literal_spans(text)
            for match in _CONTEXT_FILE_TARGET_RE.finditer(text[start:end])
        )
    )


_DIRECT_PROTECTED_TEST_FILE_TARGET_RE = re.compile(
    r"\b(?:re[- ]?run|run|execute|rnu)\b"
    r"[^,，。！？!?;；\n]{0,48}"
    r"\b(?:pytest|tests?|(?:test\s+)?suite)\b"
    r"[^,，。！？!?;；\n]{0,24}[`\"'“‘]|"
    r"(?:\b(?:re[- ]?run|run|execute|rnu)\b|"
    r"(?:重跑|运行|执行|跑(?:下|一下|一遍)?))"
    r"[^,，。！？!?;；\n]{0,24}[`\"'“‘]"
    r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|c|h|md|"
    r"json|yaml|yml|toml)[`\"'”’]"
    r"[^,，。！？!?;；\n]{0,20}(?:tests?|pytest|测试|单测|测例|用例)",
    re.IGNORECASE,
)
_INLINE_CODE_FILE_ACTION_TARGET_RE = re.compile(
    r"(?:\b(?:modify|update|change|edit|touch|fix|repair|refactor|rename|"
    r"optimi[sz]e|tune)\b|"
    r"(?:修改|更新|调整|"
    rf"改(?!{_VIEW_SWITCH_AFTER_CHANGE_PATTERN})(?:一?下)?|"
    r"触碰|碰|修复|修|重构|优化|调优|重命名|改名))"
    r"\s*(?:the\s+)?[`\"'“‘](?P<target>[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|"
    r"java|go|rs|cpp|c|h|md|json|yaml|yml|toml))[`\"'”’]",
    re.IGNORECASE,
)
_CONTEXT_NAMED_ARTIFACT_RE = re.compile(
    r"\b(?:readme|docs?|documentation)\b",
    re.IGNORECASE,
)


def _isolated_protected_named_targets(text: str) -> frozenset[str]:
    """Return standalone quoted README/docs targets, not quoted prose."""

    targets: set[str] = set()
    quote_characters = "`\"'“”‘’ \t\r\n"
    for start, end in _protected_intent_literal_spans(text):
        candidate = text[start:end].strip(quote_characters)
        if _CONTEXT_NAMED_ARTIFACT_RE.fullmatch(candidate):
            targets.add(candidate.casefold())
    return frozenset(targets)
_CONTEXT_STRUCTURAL_SPLIT_RE = re.compile(
    r"(?:按|照)\s*(?:刚才|上面|前面|之前)(?:说的|划定的|标出的|的)?"
    r"\s*(?:(?:工作流|提交|任务)\s*)?(?:边界\s*)?"
    r"(?:拆开|拆分|分开)",
    re.IGNORECASE,
)
_CONTEXT_APPLY_PLAN_RE = re.compile(
    r"^\s*(?:按这个方案做|照刚才(?:说的)?落地|按你说的(?:做|落地)|"
    r"apply\s+that\s+plan|put\s+that\s+plan\s+into\s+practice)"
    r"\s*[。.!！]?\s*$",
    re.IGNORECASE,
)
_CONTEXT_CUE_DENIAL_PREFIX_RE = re.compile(
    r"(?:^|[,，。！？!?;；])\s*"
    r"(?:不要|别|甭|不再|不准|不许|不得|禁止|请勿|勿|不)\s*$|"
    r"(?:^|[,.;!?])\s*(?:do\s+not|don['’]?t|never)\s*$",
    re.IGNORECASE,
)
_CONTEXT_CUE_DENIAL_SUFFIX_RE = re.compile(
    r"^[^,，。！？!?;；\n]{0,32}"
    r"(?:不要|别|甭|不准|不许|不得|禁止|请勿|勿|不)\s*"
    r"(?:继续|接着|做|处理|落地|优化|调优|拆开|拆分|分开|"
    r"修改|更新|创建|执行|运行|跑|检查|核对|审查|查|看)|"
    r"^[^,，。；;.!！？?\n]{0,32}\b(?:do\s+not|don['’]?t|never)\s+"
    r"(?:continue|proceed|do|apply|optimi[sz]e|split|update|create|"
    r"execute|run|check|review|inspect)\b",
    re.IGNORECASE,
)
_CONTEXT_FACET_REFERENCE_TERMS = frozenset(
    {
        "analysis",
        "area",
        "cause",
        "design",
        "digging",
        "further",
        "going",
        "hang",
        "inspect",
        "investigate",
        "investigating",
        "issue",
        "keep",
        "latency",
        "look",
        "looking",
        "one",
        "problem",
        "profile",
        "profiling",
        "reason",
        "review",
        "root",
        "same",
        "slow",
        "spot",
        "why",
    }
)

_CONTEXT_REUSE_ANCHOR_RE = re.compile(
    r"^\s*(?:(?:嗯|行|好|对|那就|yep|yeah|yes|ok(?:ay)?)\s*[,，]?\s*)?"
    r"(?:"
    r"(?:就它|就这个|还是这个|还是它)(?=$|[\s,，。！？])|"
    r"(?:就按|按|照)(?:上面|前面|之前)(?:说的)?"
    r"(?=$|[\s,，。！？]|做|改|修|处理)|"
    r"照刚才(?:说的)?那个(?:做|弄|处理)|"
    r"(?:这个(?:再)?|接着)?往下(?:捋|刨|查|看|走)|"
    r"(?:就)?照(?:着)?这个(?:(?:方案)?(?:收拾|弄|做|处理)|"
    r"(?=$|[\s,，。！？]))|"
    r"(?:那就)?按你说的(?:(?:弄|做|处理)|(?=$|[\s,，。！？]))|"
    r"\bdo\s+that\b|"
    r"\bsame(?:\s+(?:thing|one|query)|\s+as\s+above)\b|"
    r"\bkeep\s+going\b"
    r")",
    re.IGNORECASE,
)
_CONTEXT_PERFORMANCE_CONTINUATION_RE = re.compile(
    r"^\s*(?:它|这个|那个|该(?:接口|查询|任务)?)\s*"
    r"(?:慢|性能|延迟|耗时)(?:的)?(?:问题|情况|事)?\s*"
    r"(?:继续|接着)",
    re.IGNORECASE,
)
_CONTEXT_PERFORMANCE_TARGET_RE = re.compile(
    r"\b(?:query|sql|database|api|endpoint|startup)\b|"
    r"(?:查询|数据库|接口|启动)",
    re.IGNORECASE,
)
_PERFORMANCE_CONTEXT_TOPICS = frozenset(
    {"profile", "performance", "database", "query", "sql", "latency"}
)
_PERFORMANCE_SIGNAL_CONTEXT_TOPICS = frozenset(
    {"profile", "performance", "latency", "slow"}
)
_TEST_CONTEXT_TOPICS = frozenset(
    {"pytest", "test", "tests", "failure", "assertion", "coverage"}
)
_DIAGNOSTIC_CONTEXT_TOPICS = frozenset(
    {
        "concurrency",
        "contention",
        "deadlock",
        "failure",
        "hang",
        "latency",
        "lock",
        "performance",
        "profile",
        "pytest",
        "slow",
        "startup",
        "test",
        "tests",
        "timeout",
    }
)
_PRE_UPDATE_TEST_ORDER_RE = re.compile(
    r"(?:升级|更新|升一?下)\s*"
    r"(?:依赖|包|版本|dependencies?|packages?|libraries?)\s*"
    r"(?:前|之前)\s*先\s*跑测(?:试)?",
    re.IGNORECASE,
)
_CONTEXT_ONLY_RENAME_RE = re.compile(
    _CONTEXT_ONLY_RENAME_PATTERN,
    re.IGNORECASE,
)
_CONTEXT_TOPIC_EXTENSION_RE = re.compile(
    r"^\s*(?:and|what\s+about)\s+(?:the\s+)?"
    r"(?:retry|error|failure|fallback|call|execution|permission|auth|data)"
    r"\s+(?:path|flow|branch|layer)\s*[?？]?\s*$",
    re.IGNORECASE,
)
_CONTEXT_READ_ONLY_EXPLAIN_RE = re.compile(
    r"\bread[\s-]*only\b[^.!?;，。！？]{0,32}"
    r"\b(?:explain|explanation|description|walkthrough)\b|"
    r"\b(?:explain|explanation|describe)\b[^.!?;，。！？]{0,32}"
    r"\bread[\s-]*only\b",
    re.IGNORECASE,
)
_CONTEXT_CALLER_LIST_RE = re.compile(
    r"(?:只|仅)?\s*(?:列|列出|罗列)(?:一?\s*下)?"
    r"[^\u3002！？!?;；\n]{0,32}(?:调用方|谁调用|调用点)|"
    r"\b(?:just|only)\s+list\b[^.!?;\n]{0,32}\bcallers?\b",
    re.IGNORECASE,
)
_CONTEXT_INTENT_ONLY_RE = re.compile(
    r"(?:这个也一起改掉|你修一下吧)|\b(?:fix it|change that too)\b",
    re.IGNORECASE,
)

_PRIOR_RESULT_REFERENCE_RE = re.compile(
    r"\b(?:it|them|those|the\s+(?:findings?|results?|warnings?|errors?))\b|"
    r"\bwhat\s+(?:it|they|the\s+(?:tool|command|check|linter))\s+"
    r"(?:finds?|reports?|flags?)\b|"
    r"(?:它|它们|这些|上述|前述)(?:发现|报告|标记)?(?:的)?(?:问题|结果|警告|错误)?",
    re.IGNORECASE,
)

_UNSAFE_CONTEXT_ACTIONS = frozenset(
    {
        ActionType.DELETE,
        ActionType.EXECUTE,
        ActionType.MERGE,
        ActionType.MOVE,
        ActionType.RENAME,
        ActionType.SPLIT,
    }
)
_READ_ONLY_CONTEXT_ACTIONS = frozenset(
    {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
)
_CONTEXT_INHERITABLE_ACTIONS = frozenset(
    {
        ActionType.READ,
        ActionType.ANALYZE,
        ActionType.COMPARE,
        ActionType.CREATE,
        ActionType.UPDATE,
    }
)
_RETRACTED_CONSTRAINT_ACTIONS = frozenset(
    {
        ActionType.CREATE,
        ActionType.UPDATE,
        ActionType.DELETE,
        ActionType.EXECUTE,
        ActionType.MERGE,
        ActionType.MOVE,
        ActionType.RENAME,
        ActionType.SPLIT,
    }
)
_RETRACTED_EXECUTION_TOPIC_RE = re.compile(
    r"\b(?:pytest|pytes|tests?|test\s+(?:cases?|suite|results?)|"
    r"failures?|tracebacks?|assertions?|coverage)\b|"
    r"(?:pytest|测试|单测|用例|失败|报错|堆栈|断言|覆盖率)",
    re.IGNORECASE,
)
SEMANTIC_INTENT_ALLOWED_ACTIONS: Mapping[IntentType, frozenset[ActionType]] = (
    MappingProxyType(
        {
            IntentType.CODE: frozenset({ActionType.CREATE, ActionType.UPDATE}),
            IntentType.DEBUG: frozenset({ActionType.ANALYZE, ActionType.UPDATE}),
            IntentType.REFACTOR: frozenset({ActionType.UPDATE}),
            IntentType.EXPLAIN: frozenset({ActionType.READ, ActionType.ANALYZE}),
            IntentType.SEARCH: frozenset({ActionType.READ}),
            IntentType.REVIEW: frozenset(
                {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
            ),
            IntentType.TEST: frozenset({ActionType.CREATE, ActionType.ANALYZE}),
            IntentType.DOCUMENT: frozenset(
                {ActionType.CREATE, ActionType.READ, ActionType.UPDATE}
            ),
            IntentType.CONFIGURE: frozenset({ActionType.CREATE, ActionType.UPDATE}),
            IntentType.QUESTION: frozenset({ActionType.READ, ActionType.ANALYZE}),
            IntentType.CHAT: frozenset({ActionType.READ}),
            IntentType.MEMORY: frozenset(
                {ActionType.CREATE, ActionType.READ, ActionType.UPDATE}
            ),
        }
    )
)
_ACTION_PRIORITY: dict[ActionType, int] = {
    ActionType.DELETE: 100,
    ActionType.RENAME: 90,
    ActionType.MOVE: 90,
    ActionType.MERGE: 90,
    ActionType.SPLIT: 90,
    ActionType.CREATE: 80,
    ActionType.UPDATE: 80,
    ActionType.EXECUTE: 70,
    ActionType.COMPARE: 55,
    ActionType.ANALYZE: 50,
    ActionType.READ: 40,
    ActionType.UNKNOWN: 0,
}
_SAFE_CONTEXT_TOPICS = frozenset(
    {
        "agent-orchestration",
        "architecture",
        "assertion",
        "authentication",
        "backend",
        "branch",
        "commit",
        "code",
        "call flow",
        "component",
        "config",
        "context",
        "compaction",
        "concurrency",
        "contention",
        "database",
        "deadlock",
        "deployment-automation",
        "documentation",
        "deploy",
        "delegation",
        "frontend",
        "failure",
        "function",
        "git",
        "git-workflow",
        "implementation",
        "hang",
        "latency",
        "lock",
        "memory",
        "mobile",
        "multi-agent",
        "module",
        "parser",
        "permission",
        "performance",
        "preference",
        "profile",
        "provenance",
        "pytest",
        "pipeline",
        "project",
        "query",
        "regression",
        "release",
        "retrieval",
        "routing",
        "runtime",
        "skill",
        "security",
        "summary",
        "slow",
        "startup",
        "test",
        "timeout",
    }
)


@dataclass(frozen=True, slots=True)
class _PatternCandidate:
    intent_type: IntentType
    action_type: ActionType
    confidence: float
    match_span: tuple[int, int]
    trigger_span: tuple[int, int]
    pattern_order: int


@dataclass(frozen=True, slots=True)
class _ContextContinuationCue:
    span: tuple[int, int]
    safe_override: tuple[IntentType, ActionType] | None = None
    read_only_probe: bool = False
    actionful: bool = False


_CONTEXT_SUBJECT_FILLER_RE = re.compile(
    r"\b(?:also|again|please|pls|just|now|then|but|keep|going|continue|"
    r"continued|look|looking|check|review|analy[sz]e|why|slow|so|"
    r"issue|problem|root|cause|reason|hang|latency|design|one|spot|area|"
    r"investigat(?:e|ing)|profil(?:e|ing)|dig(?:ging)?|further|"
    r"read[\s-]*only|code|on|at|with|about|the|time)\b|"
    r"(?:也|再|然后|接着|继续|往下|不过|但是|但|这次|只|仅|看看|看|查|检查|审查|分析|"
    r"排查|定位|调查|评审|复核|捋|捋捋|过|过一眼|挖|挖挖|刨|筛|"
    r"先|的|一下|一层|一轮|一遍|下去|深里|仔细|吧|呢|请|帮我|问题|毛病|"
    r"情况|为什么|为何|慢|延迟|原因|根因|优缺点|代码|这个|那个|它|就行|"
    r"瓶颈|边界|拆开|有没有|有无|是否|遗漏|层)",
    re.IGNORECASE,
)


def _context_subject_shadows_prior(
    matching_text: str,
    cue_span: tuple[int, int],
    *,
    performance_anaphor: bool,
    current_entities: Mapping[str, Sequence[str]] | None = None,
    context_entities: Mapping[str, Sequence[str]] | None = None,
    context_reference_terms: frozenset[str] = frozenset(),
    context_topics: Sequence[str] = (),
    current_topics: Sequence[str] = (),
) -> bool:
    """Reject continuation when the current turn introduces another domain."""

    projected = _affirmed_evidence_projection(matching_text)
    if _context_has_new_concrete_target(
        projected,
        current_entities=current_entities,
        context_entities=context_entities,
        context_reference_terms=context_reference_terms,
        context_topics=context_topics,
    ):
        return True
    if re.search(
        r"(?:写|补|创建|新增|添加).{0,12}回归(?:测试|用例)?|"
        r"\b(?:write|add|create)\b.{0,24}\bregression\b",
        projected,
        re.IGNORECASE,
    ):
        return False
    characters = list(projected)
    for index in range(cue_span[0], cue_span[1]):
        if characters[index] not in "\r\n":
            characters[index] = " "
    remainder = "".join(characters)
    current_targets = _context_entity_values(current_entities)
    prior_targets = _context_entity_values(context_entities)
    for target in sorted(current_targets & prior_targets, key=len, reverse=True):
        remainder = re.sub(
            re.escape(target),
            lambda match: " " * len(match.group(0)),
            remainder,
            flags=re.IGNORECASE,
        )
    for term in sorted(context_reference_terms, key=len, reverse=True):
        remainder = re.sub(
            rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
            lambda match: " " * len(match.group(0)),
            remainder,
            flags=re.IGNORECASE,
        )
    for topic in sorted(context_topics, key=len, reverse=True):
        remainder = re.sub(
            rf"(?<![a-z0-9_]){re.escape(topic)}(?![a-z0-9_])",
            lambda match: " " * len(match.group(0)),
            remainder,
            flags=re.IGNORECASE,
        )
    shared_topics = {
        str(topic).strip().casefold() for topic in context_topics
    }.intersection(
        str(topic).strip().casefold() for topic in current_topics
    )
    masked_skill_directive = False
    if shared_topics:
        # An explicitly selected Skill is a routing constraint, not a new task
        # subject.  Ignore it here only when the current turn independently
        # repeats a normalized topic from the active task.
        for pattern in _EXPLICIT_SKILL_IDENTIFIER_PATTERNS:
            updated, count = pattern.subn(
                lambda match: " " * len(match.group(0)),
                remainder,
            )
            remainder = updated
            masked_skill_directive = masked_skill_directive or bool(count)
        for chinese_term, aliases in _CHINESE_KEYWORD_ALIASES:
            if shared_topics.intersection(alias.casefold() for alias in aliases):
                remainder = re.sub(
                    re.escape(chinese_term),
                    lambda match: " " * len(match.group(0)),
                    remainder,
                    flags=re.IGNORECASE,
                )
    if masked_skill_directive:
        remainder = re.sub(
            r"\b(?:correction|corrected)\b|(?:更正|纠正)",
            " ",
            remainder,
            flags=re.IGNORECASE,
        )
    remainder = re.sub(r"(?:重命名|改名)", " ", remainder, flags=re.IGNORECASE)
    remainder = _REFERENCE_RE.sub(" ", remainder)
    remainder = ACTION_MARKER_RE.sub(" ", remainder)
    remainder = _CONTEXT_SUBJECT_FILLER_RE.sub(" ", remainder)
    return _has_meaningful_context_residual(remainder)


def _has_meaningful_context_residual(text: str) -> bool:
    """Treat every remaining token or symbol as a possible new target.

    Context inheritance is an authority boundary.  One-character targets,
    short initialisms, digits, and emoji therefore cannot be discarded merely
    because they are shorter than a conventional identifier.
    """

    return any(
        character.isalnum()
        or unicodedata.category(character).startswith("S")
        for character in text
    )


def _context_entity_values(
    entities: Mapping[str, Sequence[str]] | None,
) -> frozenset[str]:
    if not entities:
        return frozenset()
    return frozenset(
        str(value).strip().casefold()
        for key in ("files", "functions", "classes")
        for value in entities.get(key, ())
        if str(value).strip()
    )


def _context_has_new_concrete_target(
    matching_text: str,
    *,
    current_entities: Mapping[str, Sequence[str]] | None,
    context_entities: Mapping[str, Sequence[str]] | None,
    context_reference_terms: frozenset[str],
    context_topics: Sequence[str] = (),
) -> bool:
    """Keep a continuation from crossing an explicit task/target switch."""

    literal_masked = _mask_protected_intent_literals(matching_text)
    if _CONTEXT_EXPLICIT_TARGET_SWITCH_RE.search(literal_masked):
        return True
    visible_text = literal_masked.casefold()
    visible_entity_targets = frozenset(
        target
        for target in _context_entity_values(current_entities)
        if target in visible_text
    )
    protected_action_targets = frozenset(
        match.group("target").casefold()
        for match in _INLINE_CODE_FILE_ACTION_TARGET_RE.finditer(matching_text)
    )
    isolated_literal_targets = frozenset(
        target.casefold()
        for target in _isolated_protected_file_targets(matching_text)
    )
    current_targets = (
        visible_entity_targets
        | protected_action_targets
        | isolated_literal_targets
        | frozenset(
            match.group(1).strip().casefold()
            for match in _CONTEXT_FILE_TARGET_RE.finditer(literal_masked)
        )
    )
    prior_targets = _context_entity_values(context_entities)
    if current_targets and (
        not prior_targets or not current_targets.issubset(prior_targets)
    ):
        return True
    named_targets = {
        match.group(0).casefold()
        for match in _CONTEXT_NAMED_ARTIFACT_RE.finditer(literal_masked)
    } | set(_isolated_protected_named_targets(matching_text))
    known_terms = context_reference_terms | {
        str(topic).strip().casefold() for topic in context_topics
    }
    return bool(named_targets - known_terms)


def _context_denies_prior_target(
    text: str,
    cue_span: tuple[int, int],
    context_entities: Mapping[str, Sequence[str]] | None,
) -> bool:
    """Reject a continuation after the current turn dismissed its old target."""

    prior_targets = _context_entity_values(context_entities)
    if not prior_targets:
        return False
    literal_masked = _mask_protected_intent_literals(text)
    projected = _affirmed_evidence_projection(literal_masked)
    prefix = literal_masked[: cue_span[0]]
    projected_prefix = projected[: cue_span[0]]
    projected_suffix = projected[cue_span[1] :]
    for target in prior_targets:
        escaped = re.escape(target)
        if re.search(escaped, projected_suffix, re.IGNORECASE):
            continue
        if re.search(escaped, prefix, re.IGNORECASE) is None:
            continue
        target_then_abandon = re.search(
            escaped
            + r"[^,，。！？!?;；\n]{0,16}"
            + r"(?:不用|不要|别|甭|无需|无须|不必|不再)"
            + r"(?:再)?(?:看|查|检查|处理|管|使用|用)?(?:了|啦)?",
            prefix,
            re.IGNORECASE,
        )
        abandon_then_target = re.search(
            r"(?:不用|不要|别|甭|无需|无须|不必|不再)"
            r"(?:再)?(?:看|查|检查|处理|管|使用|用)"
            r"[^,，。！？!?;；\n]{0,16}"
            + escaped
            + r"|\b(?:do\s+not|don['’]?t|never)\s+"
            r"(?:look\s+at|inspect|check|review|use|handle)\s+"
            + escaped,
            prefix,
            re.IGNORECASE,
        )
        if target_then_abandon is not None or abandon_then_target is not None:
            return True
        # A negated mutation still identifies its object: ``不要修改 api.py，
        # 继续检查`` keeps api.py available to the later read-only probe.  Only
        # the explicit abandonment grammars above remove a prior target.
        if re.search(escaped, projected_prefix, re.IGNORECASE) is None:
            continue
    return False


def _context_cue_is_negated(
    matching_text: str,
    cue_span: tuple[int, int],
) -> bool:
    """Return whether the continuation itself, rather than another action, is denied."""

    projected = _affirmed_evidence_projection(matching_text)
    start, end = cue_span
    if not _has_meaningful_context_residual(projected[start:end]):
        return True
    if _CONTEXT_CUE_DENIAL_PREFIX_RE.search(matching_text[:start]):
        return True
    return bool(_CONTEXT_CUE_DENIAL_SUFFIX_RE.search(matching_text[end:]))


def _context_structural_split_pair(
    context_topics: Sequence[str],
    text: str = "",
) -> tuple[IntentType, ActionType] | None:
    """Map an explicit boundary split to one of two bounded domain actions."""

    topics = {str(topic).strip().casefold() for topic in context_topics}
    names_git_boundary = bool(
        re.search(r"(?:提交|工作流)|\b(?:commit|workflow)\b", text, re.IGNORECASE)
    )
    names_task_boundary = bool(
        re.search(r"(?:任务)|\btask\b", text, re.IGNORECASE)
    )
    git_context = "git-workflow" in topics
    agent_context = "agent-orchestration" in topics
    if names_git_boundary:
        return (
            (IntentType.CONFIGURE, ActionType.UPDATE)
            if git_context
            else None
        )
    if names_task_boundary:
        return (IntentType.CODE, ActionType.CREATE) if agent_context else None
    if agent_context:
        return IntentType.CODE, ActionType.CREATE
    if git_context:
        return IntentType.CONFIGURE, ActionType.UPDATE
    return None


def _context_apply_plan_pair(
    context_topics: Sequence[str],
) -> tuple[IntentType, ActionType] | None:
    """Resolve an explicit plan-application command from a bounded domain."""

    topics = {str(topic).strip().casefold() for topic in context_topics}
    if "agent-orchestration" in topics:
        return IntentType.CODE, ActionType.CREATE
    if (
        "git-workflow" in topics
        or "deployment-automation" in topics
    ):
        return IntentType.CONFIGURE, ActionType.UPDATE
    return None


def _context_update_intent(
    context_topics: Sequence[str],
) -> IntentType | None:
    """Specialize a present-turn update without changing its authority."""

    topics = {str(topic).strip().casefold() for topic in context_topics}
    if "documentation" in topics:
        return IntentType.DOCUMENT
    if topics.intersection({"frontend", "mobile"}):
        return IntentType.CODE
    return None


def _read_only_probe_pair(
    previous: IntentOperation | None,
    context_topics: Sequence[str] = (),
    text: str = "",
) -> tuple[IntentType, ActionType]:
    """Resolve an actionful probe without replaying prior side effects."""

    normalized_topics = {str(topic).casefold() for topic in context_topics}
    if re.search(r"\bcompare\b|(?:比较|对比|优缺点)", text, re.IGNORECASE):
        return IntentType.REVIEW, ActionType.COMPARE
    if re.search(
        r"\bexplain(?:ing)?\b|(?:解释|说明|讲讲|说说)",
        text,
        re.IGNORECASE,
    ):
        return IntentType.EXPLAIN, ActionType.READ
    if re.search(
        r"\bsearch(?:ing)?\b|(?:搜索|查找)",
        text,
        re.IGNORECASE,
    ):
        return IntentType.SEARCH, ActionType.READ
    if normalized_topics.intersection(_DIAGNOSTIC_CONTEXT_TOPICS):
        return IntentType.DEBUG, ActionType.ANALYZE
    if (
        previous is not None
        and previous.intent_type is IntentType.EXPLAIN
        and previous.action_type in _READ_ONLY_CONTEXT_ACTIONS
    ):
        return IntentType.EXPLAIN, ActionType.READ
    if re.search(_CONTEXT_SAFE_PROBE_FRAGMENT, text, re.IGNORECASE):
        if (
            previous is not None
            and previous.intent_type is IntentType.DEBUG
            and previous.action_type in _READ_ONLY_CONTEXT_ACTIONS
        ):
            return IntentType.DEBUG, ActionType.ANALYZE
        return IntentType.REVIEW, ActionType.ANALYZE
    if previous is None or previous.action_type not in _READ_ONLY_CONTEXT_ACTIONS:
        return IntentType.REVIEW, ActionType.ANALYZE
    return previous.intent_type, previous.action_type


def _raw_context_continuation_match(
    matching_text: str,
) -> tuple[re.Match[str] | None, bool, bool]:
    performance_match = _CONTEXT_PERFORMANCE_CONTINUATION_RE.search(
        matching_text
    )
    read_only_probe_match = _CONTEXT_COMPOSITIONAL_READ_ONLY_RE.search(
        matching_text
    )
    structural_split_match = _CONTEXT_STRUCTURAL_SPLIT_RE.search(
        matching_text
    )
    reason_probe_match = _CONTEXT_REASON_PROBE_RE.search(matching_text)
    apply_plan_match = _CONTEXT_APPLY_PLAN_RE.search(matching_text)
    match = (
        performance_match
        or read_only_probe_match
        or structural_split_match
        or reason_probe_match
        or apply_plan_match
        or _CONTEXT_REUSE_ANCHOR_RE.search(matching_text)
        or _CONTEXT_TOPIC_EXTENSION_RE.search(matching_text)
        or _CONTINUATION_RE.search(matching_text)
        or _CONTEXT_REUSE_RE.search(matching_text)
        or _CONTEXT_SUFFIX_CONTINUATION_RE.search(matching_text)
    )
    return (
        match,
        performance_match is not None and match is performance_match,
        bool(
            (read_only_probe_match is not None and match is read_only_probe_match)
            or (reason_probe_match is not None and match is reason_probe_match)
            or (performance_match is not None and match is performance_match)
        ),
    )


def _context_subject_switch(text: str) -> bool:
    """Return whether a continuation cue is shadowed by a new current topic."""

    matching_text = _mask_protected_intent_literals(
        _mask_superseded_intent_prefix(text)
    )
    match, performance_anaphor, _read_only_probe = (
        _raw_context_continuation_match(matching_text)
    )
    return bool(
        match is not None
        and _context_subject_shadows_prior(
            matching_text,
            match.span(),
            performance_anaphor=performance_anaphor,
        )
    )


def _context_continuation_cue(
    text: str,
    *,
    current_entities: Mapping[str, Sequence[str]] | None = None,
    context_entities: Mapping[str, Sequence[str]] | None = None,
    context_reference_terms: frozenset[str] = frozenset(),
    context_topics: Sequence[str] = (),
    current_topics: Sequence[str] = (),
) -> _ContextContinuationCue | None:
    """Recognize a bounded current-turn reference to a prior safe operation."""

    if len(text) > 240:
        return None
    matching_text = _mask_protected_intent_literals(
        _mask_superseded_intent_prefix(text)
    )
    match, performance_anaphor, read_only_probe = (
        _raw_context_continuation_match(matching_text)
    )
    if match is None:
        return None
    if _context_cue_is_negated(matching_text, match.span()):
        return None
    if _context_has_new_concrete_target(
        _mask_superseded_intent_prefix(text),
        current_entities=current_entities,
        context_entities=context_entities,
        context_reference_terms=context_reference_terms,
        context_topics=context_topics,
    ):
        return None
    if _context_denies_prior_target(
        _mask_superseded_intent_prefix(text),
        match.span(),
        context_entities,
    ):
        return None
    structural_split = bool(
        (structural_match := _CONTEXT_STRUCTURAL_SPLIT_RE.search(matching_text))
        is not None
        and structural_match.span() == match.span()
    )
    apply_plan = bool(
        (apply_match := _CONTEXT_APPLY_PLAN_RE.search(matching_text)) is not None
        and apply_match.span() == match.span()
    )
    if _CONTEXT_READ_ONLY_EXPLAIN_RE.search(matching_text):
        override = (IntentType.EXPLAIN, ActionType.READ)
    elif _CONTEXT_CALLER_LIST_RE.search(matching_text):
        override = (IntentType.SEARCH, ActionType.READ)
    elif structural_split:
        override = _context_structural_split_pair(context_topics, matching_text)
    elif apply_plan:
        override = _context_apply_plan_pair(context_topics)
    else:
        override = None
    if (
        (override is None or structural_split or apply_plan)
        and _context_subject_shadows_prior(
            matching_text,
            match.span(),
            performance_anaphor=performance_anaphor,
            current_entities=current_entities,
            context_entities=context_entities,
            context_reference_terms=context_reference_terms,
            context_topics=context_topics,
            current_topics=current_topics,
        )
    ):
        return None
    return _ContextContinuationCue(
        span=match.span(),
        safe_override=override,
        read_only_probe=read_only_probe,
        actionful=bool(read_only_probe or structural_split or apply_plan),
    )


class IntentParser:
    def __init__(self, semantic_adapter: IntentSemanticAdapter | None = None):
        self._pattern_cache: list[tuple[re.Pattern[str], IntentType, ActionType]] = []
        self._semantic_adapter = semantic_adapter
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        for pattern, intent, action in _ALL_PATTERNS:
            try:
                self._pattern_cache.append(
                    (re.compile(pattern, re.IGNORECASE), intent, action)
                )
            except re.error:
                logger.warning("Invalid pattern: %s", pattern)

    def parse(
        self,
        user_input: str,
        *,
        context: IntentContext | None = None,
        call_context: Mapping[str, Any] | None = None,
    ) -> ParsedIntent:
        return self._parse_internal(
            user_input,
            context=context,
            call_context=call_context,
            allow_semantic=True,
        )

    def _parse_internal(
        self,
        user_input: str,
        *,
        context: IntentContext | None,
        call_context: Mapping[str, Any] | None,
        allow_semantic: bool,
    ) -> ParsedIntent:
        if not user_input or not user_input.strip():
            return ParsedIntent(
                raw_input=user_input,
                intent_type=IntentType.UNKNOWN,
                action_type=ActionType.UNKNOWN,
                confidence=0.0,
                decision_reason="empty_input",
                semantic_query=str(user_input or ""),
            )

        text = user_input.strip()
        bounded_context = (
            context if isinstance(context, IntentContext) else IntentContext()
        )
        previous_intent = self._previous_intent(bounded_context)
        context_frame_conflict = self._context_frame_conflicts(
            bounded_context,
            previous_intent,
        )
        context_entities, context_topics = self._context_topics(
            bounded_context,
            include_previous=not context_frame_conflict,
        )
        context_reference_terms = self._context_reference_terms(bounded_context)
        context_available = bool(
            any(context_entities.values())
            or context_topics
            or previous_intent is not None
            or bounded_context.active_task
            or bounded_context.recent_turns
        )
        operations, evidence, context_used = self._detect_operations(
            text,
            context_entities=context_entities,
            context_topics=context_topics,
            context_reference_terms=context_reference_terms,
            context_available=context_available,
            previous_intent=previous_intent,
            context_frame_conflict=context_frame_conflict,
        )

        operations, evidence, reference_context_used, referenced_files = (
            self._bind_current_file_references(
                text,
                operations,
                evidence,
                bounded_context,
                previous_intent=previous_intent,
                context_frame_conflict=context_frame_conflict,
            )
        )
        context_used = context_used or reference_context_used
        if referenced_files is not None:
            context_entities = {**context_entities, "files": list(referenced_files)}

        semantic_status = "disabled"
        warnings: list[str] = []
        if self._semantic_adapter is not None:
            # A semantic provider may propose only affirmative operations. If
            # the deterministic layer has not understood an explicit
            # negation, letting the provider fill the gap could invert "do not
            # touch" into write authority. Conservatively abstain instead.
            semantic_target = self._semantic_fallback_target(text, operations)
            # Whole-request fallback remains fail-closed when deterministic
            # parsing could not scope an explicit negation.  A partial target
            # already has a local clause span and was individually checked by
            # ``_semantic_fallback_target``, so a separate earlier negative
            # clause must not suppress that safe affirmative fragment.
            unresolved_negation = bool(
                semantic_target is not None
                and semantic_target[1] is None
                and is_clause_negated(semantic_target[0])
            )
            if not allow_semantic or semantic_target is None or unresolved_negation:
                semantic_status = "skipped"
            else:
                semantic_status = "used"
                semantic_text, semantic_span = semantic_target
                semantic_context = (
                    IntentContext()
                    if context_available and _context_subject_switch(text)
                    else bounded_context
                )
                try:
                    proposals = self._semantic_adapter.infer(
                        SemanticIntentRequest(
                            text=semantic_text,
                            context=semantic_context,
                            deterministic_operations=operations,
                        ),
                        call_context=call_context,
                    )
                    semantic_operations, semantic_evidence = (
                        self._validate_semantic_proposals(
                            proposals,
                            semantic_text,
                            span=semantic_span,
                        )
                    )
                    operations = self._merge_semantic_operations(
                        operations,
                        semantic_operations,
                    )
                    if semantic_span is None:
                        evidence += semantic_evidence
                    else:
                        evidence = self._merge_semantic_evidence(
                            evidence,
                            semantic_evidence,
                        )
                    if semantic_operations and (
                        semantic_context.recent_turns
                        or semantic_context.previous_intent is not None
                        or bool(semantic_context.active_task)
                    ):
                        # The semantic classifier received this bounded context
                        # as input. Preserve its safe topics/entities for Skill
                        # routing instead of pretending the decision was
                        # context-free after a successful proposal.
                        context_used = True
                    # Production adapters expose only a low-cardinality,
                    # content-free outcome.  Keep legacy/custom adapters at
                    # ``used`` so this optional diagnostic cannot change their
                    # behavior or surface arbitrary provider text.
                    try:
                        adapter_status = getattr(
                            self._semantic_adapter, "last_status", None
                        )
                    except Exception:  # noqa: BLE001 - diagnostics are optional
                        adapter_status = None
                    if adapter_status in _SEMANTIC_DIAGNOSTIC_STATUSES:
                        semantic_status = adapter_status
                except Exception as error:
                    try:
                        from minicode.turn_cancellation import TurnCancellationRequested
                    except ImportError:  # pragma: no cover - package invariant
                        TurnCancellationRequested = ()  # type: ignore[assignment,misc]
                    if TurnCancellationRequested and isinstance(
                        error, TurnCancellationRequested
                    ):
                        raise
                    if type(error).__name__.endswith(
                        ("DeadlineExceeded", "BudgetExceeded")
                    ):
                        raise
                    semantic_status = "failed"
                    warnings.append(f"semantic_adapter_failed:{type(error).__name__}")

        primary_index = self._select_primary_operation(operations)
        if primary_index is None:
            intent_type = IntentType.UNKNOWN
            action_type = ActionType.UNKNOWN
            confidence = 0.0
        else:
            primary = operations[primary_index]
            intent_type = primary.intent_type
            action_type = primary.action_type
            confidence = primary.confidence

        entities = self._extract_entities(text)
        visible_or_named_files = set(entities["files"]) | set(_isolated_protected_file_targets(text))
        current_file_reference = has_file_reference(_mask_protected_intent_literals(text))
        operation_files = self._stable_values(
            [
                target
                for operation in operations
                for target in operation.entities.get("files", ())
                if context_used or target in visible_or_named_files or current_file_reference
            ]
        )
        if context_used:
            context_projection = dict(context_entities)
            if operation_files:
                context_projection["files"] = operation_files
            entities = self._merge_entities(entities, context_projection)
        entities = self._merge_entities(entities, {"files": operation_files})
        keywords = self._extract_keywords(text)
        if context_used:
            for topic in context_topics:
                if topic not in keywords and len(keywords) < _MAX_KEYWORDS:
                    keywords.append(topic)
        complexity = self._estimate_complexity(text, intent_type, keywords)
        semantic_query = text
        if context_used:
            topic_values = self._flatten_context_topics(
                context_entities, context_topics
            )
            if topic_values:
                semantic_query = f"{text}\nContext topics: {' '.join(topic_values)}"

        affirmed_count = sum(
            operation.polarity is IntentPolarity.AFFIRMED for operation in operations
        )
        if primary_index is not None:
            if any(item.source is EvidenceSource.SEMANTIC for item in evidence):
                decision_reason = "semantic_fallback"
            elif context_used:
                decision_reason = "contextual_evidence"
            elif affirmed_count > 1:
                decision_reason = "compound_direct_evidence"
            else:
                decision_reason = "direct_evidence"
        elif operations:
            decision_reason = "explicit_negation_only"
        else:
            decision_reason = "no_evidence"

        return ParsedIntent(
            raw_input=text,
            intent_type=intent_type,
            action_type=action_type,
            confidence=confidence,
            entities=entities,
            keywords=keywords,
            complexity_hint=complexity,
            operations=operations,
            primary_operation_index=primary_index,
            evidence=evidence,
            decision_reason=decision_reason,
            diagnostics=IntentDiagnostics(
                semantic_status=semantic_status,
                context_turns_used=(
                    len(bounded_context.recent_turns) if context_used else 0
                ),
                warnings=tuple(warnings),
            ),
            semantic_query=semantic_query,
            context_used=context_used,
            routing_directive_context=tuple(
                turn.content
                for turn in bounded_context.recent_turns
                if turn.role == "user"
            ),
        )

    def _bind_current_file_references(
        self,
        text: str,
        operations: tuple[IntentOperation, ...],
        evidence: tuple[IntentEvidence, ...],
        context: IntentContext,
        *,
        previous_intent: ParsedIntent | None,
        context_frame_conflict: bool,
    ) -> tuple[
        tuple[IntentOperation, ...], tuple[IntentEvidence, ...], bool, tuple[str, ...] | None
    ]:
        """Bind an already recognized operation to a bounded file reference.

        History can supply an entity, never an operation. Read-only references
        may describe a canceled command's object; a test continuation needs an
        explicit earlier test target, not merely a file mentioned in that turn.
        """

        from minicode.intent_entity_targets import explicit_test_subject_files

        if context_frame_conflict:
            return operations, evidence, False, None
        matching_text = _mask_protected_intent_literals(text)
        if extract_file_tokens(matching_text) or _isolated_protected_file_targets(text):
            return operations, evidence, False, None
        if context.active_task:
            frame = context.active_task
        elif context.previous_intent is not None:
            frame = context.previous_intent.raw_input
        else:
            frame = next(
                (turn.content for turn in reversed(context.recent_turns) if turn.role == "user"),
                "",
            )
        candidates = self._extract_entities(frame)["files"]
        if not candidates:
            return operations, evidence, False, None
        previous = previous_intent.primary_operation if previous_intent is not None else None
        safe_previous = bool(previous and previous.action_type not in _UNSAFE_CONTEXT_ACTIONS)
        test_subjects: tuple[str, ...] = ()
        if safe_previous and previous is not None and previous.intent_type is IntentType.TEST:
            frame_intent = (
                previous_intent
                if previous_intent is not None and previous_intent.raw_input == frame
                else self._parse_internal(
                    frame, context=None, call_context=None, allow_semantic=False
                )
            )
            test_subjects = tuple(dict.fromkeys(
                target
                for prior_operation in frame_intent.affirmed_operations
                if prior_operation.intent_type is IntentType.TEST
                and prior_operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
                for target in explicit_test_subject_files(
                    _mask_protected_intent_literals(prior_operation.clause)
                )
            ))
        updated: list[IntentOperation] = []
        added: list[IntentEvidence] = []
        projection: tuple[str, ...] | None = None
        for operation in operations:
            clause = _mask_protected_intent_literals(operation.clause)
            test_extension = bool(
                operation.intent_type is IntentType.TEST
                and operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
                and safe_previous
                and previous is not None
                and previous.intent_type is IntentType.TEST
            )
            read_only = operation.action_type in _READ_ONLY_CONTEXT_ACTIONS
            if (
                operation.polarity is not IntentPolarity.AFFIRMED
                or not (read_only or test_extension)
                or not has_file_reference(clause, test_extension=test_extension)
            ):
                updated.append(operation)
                continue
            operation_candidates = (
                test_subjects
                if test_extension
                else candidates
            )
            selected = select_referenced_files(
                clause, operation_candidates, test_extension=test_extension
            )
            projection = selected
            replacement_entities = {**operation.entities, "files": selected}
            if not selected:
                # Keep the current operation but refuse an ambiguous or
                # explicitly switched target inherited by a broader rule.
                updated.append(replace(operation, entities=replacement_entities))
                continue
            code = "context:file-reference"
            updated.append(replace(
                operation,
                entities=replacement_entities,
                evidence_codes=tuple(dict.fromkeys((*operation.evidence_codes, code))),
            ))
            added.append(IntentEvidence(
                code=code,
                source=EvidenceSource.CONTEXT,
                reason="the current file reference selects an unambiguous target in the active context",
                strength=operation.confidence,
                span=operation.span,
                turn_offset=-1,
            ))
        return tuple(updated), (*evidence, *added), bool(added), projection

    def _retracted_safety_constraints(
        self,
        text: str,
        retraction: _IntentRetraction | None,
    ) -> tuple[list[IntentOperation], list[IntentEvidence]]:
        """Turn superseded mutations into explicit negative authority.

        Read-only proposals disappear when corrected.  Mutations survive only
        as denials, so a downstream Skill cannot recover write authority from
        stale words that remain in the user's turn.  Explicit prohibitions in
        an earlier correction frame are retained as well.
        """

        if retraction is None or retraction.superseded_end <= 0:
            return [], []
        strip_characters = " \t\r\n,，.;；。!?！？—-:："
        prefix = text[: retraction.superseded_end]
        previous_retraction = _latest_intent_retraction(prefix)
        frame_start = (
            previous_retraction.active_start
            if previous_retraction is not None
            else 0
        )
        raw_frame = prefix[frame_start:]
        left_stripped = raw_frame.lstrip(strip_characters)
        frame_offset = frame_start + len(raw_frame) - len(left_stripped)
        active_frame = left_stripped.rstrip(strip_characters)
        if not active_frame:
            return [], []
        prior = self._parse_internal(
            active_frame,
            context=None,
            call_context=None,
            allow_semantic=False,
        )
        retained: list[IntentOperation] = []
        retained_codes: set[str] = set()
        evidence: list[IntentEvidence] = []
        if (
            not prior.operations
            and _BARE_COMMIT_RETRACTION_RE.fullmatch(active_frame)
            and _DIFF_REVIEW_RE.search(text[retraction.active_start :])
        ):
            # ``提交`` is polysemous outside a repository.  A later
            # correction to a diff review is sufficient to understand the
            # retracted proposal as a Git commit without making every form or
            # expense submission a code mutation.
            code = "explicit:retracted-code.update"
            operation_span = (
                frame_offset,
                frame_offset + len(active_frame),
            )
            retained.append(
                IntentOperation(
                    intent_type=IntentType.CODE,
                    action_type=ActionType.UPDATE,
                    polarity=IntentPolarity.NEGATED,
                    confidence=0.96,
                    clause=active_frame,
                    span=operation_span,
                    keywords=tuple(
                        self._stable_values(
                            [*self._extract_keywords(active_frame), "commit"]
                        )
                    ),
                    evidence_codes=(code,),
                )
            )
            retained_codes.add(code)
            evidence.append(
                IntentEvidence(
                    code=code,
                    source=EvidenceSource.EXPLICIT,
                    reason="a later diff review retracts this commit proposal",
                    strength=0.98,
                    span=operation_span,
                )
            )
        for operation in prior.operations:
            shifted_span = (
                (
                    operation.span[0] + frame_offset,
                    operation.span[1] + frame_offset,
                )
                if operation.span is not None
                else None
            )
            if operation.polarity is IntentPolarity.NEGATED:
                retained.append(replace(operation, span=shifted_span))
                retained_codes.update(operation.evidence_codes)
                continue
            if operation.action_type not in _RETRACTED_CONSTRAINT_ACTIONS:
                continue
            code = (
                f"explicit:retracted-{operation.intent_type.value}."
                f"{operation.action_type.value}"
            )
            retained.append(
                replace(
                    operation,
                    polarity=IntentPolarity.NEGATED,
                    confidence=max(operation.confidence, 0.94),
                    span=shifted_span,
                    evidence_codes=(code,),
                )
            )
            retained_codes.add(code)
            evidence.append(
                IntentEvidence(
                    code=code,
                    source=EvidenceSource.EXPLICIT,
                    reason="a later correction retracts this operation",
                    strength=0.98,
                    span=shifted_span,
                )
            )
        evidence.extend(
            replace(
                item,
                span=(
                    (
                        item.span[0] + frame_offset,
                        item.span[1] + frame_offset,
                    )
                    if item.span is not None
                    else None
                ),
            )
            for item in prior.evidence
            if item.code in retained_codes
        )
        return retained, evidence

    def _detect_operations(
        self,
        text: str,
        *,
        context_entities: dict[str, list[str]],
        context_topics: list[str],
        context_reference_terms: frozenset[str],
        context_available: bool,
        previous_intent: ParsedIntent | None,
        context_frame_conflict: bool,
    ) -> tuple[
        tuple[IntentOperation, ...],
        tuple[IntentEvidence, ...],
        bool,
    ]:
        retraction = _latest_intent_retraction(text)
        operations, evidence = self._retracted_safety_constraints(
            text,
            retraction,
        )
        context_used = False
        pending_subject: tuple[str, tuple[int, int]] | None = None
        pending_subject_read_only = False
        shared_leading_subject_keywords: list[str] = self._stable_values(
            keyword
            for operation in operations
            if any(
                str(code).startswith("explicit:retracted-")
                for code in operation.evidence_codes
            )
            for keyword in operation.keywords
        )
        detection_text = _mask_superseded_intent_prefix(text)
        matching_detection_text = _mask_protected_intent_literals(detection_text)
        question_spans = readonly_question_spans(matching_detection_text)
        reported_execution_spans = _reported_execution_spans(
            matching_detection_text
        )
        current_turn_entities = self._extract_entities(text)
        current_turn_topics = self._extract_keywords(text)
        context_cue = _context_continuation_cue(
            text,
            current_entities=current_turn_entities,
            context_entities=context_entities,
            context_reference_terms=context_reference_terms,
            context_topics=context_topics,
            current_topics=current_turn_topics,
        )
        raw_context_match, _performance_match, _read_only_match = (
            _raw_context_continuation_match(matching_detection_text)
        )
        current_turn_has_new_context_target = _context_has_new_concrete_target(
            detection_text,
            current_entities=current_turn_entities,
            context_entities=context_entities,
            context_reference_terms=context_reference_terms,
            context_topics=context_topics,
        )
        current_turn_denies_context_target = bool(
            raw_context_match is not None
            and _context_denies_prior_target(
                detection_text,
                raw_context_match.span(),
                context_entities,
            )
        )
        context_reuse_blocked = bool(
            context_frame_conflict
            or (raw_context_match is not None and context_cue is None)
        )
        if context_frame_conflict:
            context_cue = None
        performance_context_continuation = (
            _CONTEXT_PERFORMANCE_CONTINUATION_RE.search(
                matching_detection_text
            )
        )
        text_has_protected_literal = matching_detection_text != detection_text
        previous_context_operation = (
            previous_intent.primary_operation
            if previous_intent is not None
            else None
        )
        unsafe_previous_context = bool(
            previous_context_operation is not None
            and previous_context_operation.action_type in _UNSAFE_CONTEXT_ACTIONS
        )
        unsafe_effectful_context_cue = bool(
            unsafe_previous_context
            and context_cue is not None
            and not context_cue.read_only_probe
        )
        if unsafe_previous_context and (
            context_cue is None or not context_cue.read_only_probe
        ):
            # A prior destructive/execute/structural command may remain useful
            # as the object of a current read-only probe, but it can never
            # supply the missing target or domain for another effectful action.
            context_cue = None
            context_reuse_blocked = True
        context_supports_structural_rename = bool(
            not unsafe_previous_context
            and context_available
            and (
                (
                    previous_context_operation is not None
                    and previous_context_operation.intent_type is IntentType.CODE
                    and previous_context_operation.action_type is ActionType.RENAME
                )
                or any(
                    context_entities.get(key)
                    for key in ("files", "functions", "classes")
                )
                or bool(
                    context_reference_terms.intersection(
                        {
                            "class",
                            "code",
                            "file",
                            "function",
                            "method",
                            "module",
                            "package",
                            "project",
                            "repository",
                            "repo",
                            "symbol",
                        }
                    )
                )
            )
        )
        clauses = self._split_clauses(detection_text)
        for clause_index, (clause, clause_span) in enumerate(clauses):
            matching_clause = _mask_protected_intent_literals(clause)
            clause_context_match, _clause_performance, _clause_read_only = (
                _raw_context_continuation_match(matching_clause)
            )
            clause_context_denied = bool(
                clause_context_match is not None
                and _context_cue_is_negated(
                    matching_clause,
                    clause_context_match.span(),
                )
            )
            recovery_status = _RECOVERY_STATUS_RE.search(matching_clause)
            if pending_subject is not None and recovery_status is not None:
                # A reported failure belongs to the next action only while it
                # remains the active state.  "But now it works" closes that
                # discourse frame before any later request is interpreted.
                pending_subject = None
            reported_failure_context = _REPORTED_FAILURE_CONTEXT_RE.search(
                matching_clause
            )
            reported_bug_subject = bool(
                _PENDING_BUG_REPORT_RE.search(matching_clause)
                or reported_failure_context is not None
            )
            coarse_polarity = (
                IntentPolarity.NEGATED
                if is_clause_negated(matching_clause)
                else IntentPolarity.AFFIRMED
            )
            pattern_candidates = self._match_pattern_candidates(clause)
            document_continuation = _DOCUMENT_CONTENT_CONTINUATION_RE.fullmatch(
                matching_clause
            )
            if (
                document_continuation is not None
                and operations
                and operations[-1].intent_type is IntentType.DOCUMENT
                and operations[-1].action_type
                in {ActionType.CREATE, ActionType.UPDATE}
                and operations[-1].polarity is IntentPolarity.AFFIRMED
                and not (
                    any(
                        candidate.intent_type is IntentType.CODE
                        and candidate.action_type is ActionType.CREATE
                        for candidate in pattern_candidates
                    )
                    and re.search(
                        r"(?:函数|方法|接口|类|模块)[^。！？;；\n]{0,40}"
                        r"(?:来|用于|负责|打印|输出|执行|实现)",
                        matching_clause,
                    )
                )
            ):
                # ``更新 README，补上部署步骤`` supplies document content.
                # Only this explicit content complement inherits the document
                # lane; a later ``部署服务器`` keeps its own action evidence.
                pattern_candidates = (
                    _PatternCandidate(
                        intent_type=IntentType.DOCUMENT,
                        action_type=ActionType.UPDATE,
                        confidence=0.9,
                        match_span=document_continuation.span(),
                        trigger_span=document_continuation.span("action"),
                        pattern_order=-1,
                    ),
                )
            affirmed_technical, excluded_technical = (
                _technical_evidence_polarity(matching_clause)
            )
            if excluded_technical and not affirmed_technical:
                projected_candidates = self._match_pattern_candidates(
                    _affirmed_evidence_projection(matching_clause)
                )
                negated_candidates = tuple(
                    candidate
                    for candidate in pattern_candidates
                    if is_span_negated(matching_clause, candidate.trigger_span)
                )
                pattern_candidates = tuple(
                    sorted(
                        (*projected_candidates, *negated_candidates),
                        key=lambda candidate: (
                            candidate.trigger_span[0],
                            candidate.pattern_order,
                        ),
                    )
                )
            if reported_failure_context is not None:
                report_start, report_end = reported_failure_context.span()
                pattern_candidates = tuple(
                    candidate
                    for candidate in pattern_candidates
                    if not (
                        report_start
                        <= candidate.trigger_span[0]
                        < report_end
                    )
                )
            if any(
                _is_discursive_double_negation_mention(
                    matching_clause,
                    candidate.trigger_span,
                )
                for candidate in pattern_candidates
            ):
                # The double-negative verb is not authority, but its subject
                # can still resolve an immediately following real request:
                # ``不用不跑 pytest，跑完告诉我...``.
                shared_leading_subject_keywords = self._extract_keywords(
                    matching_clause
                )
            if (
                _GENERIC_CAUSE_DIAGNOSIS_RE.search(matching_clause)
                and not _has_affirmed_software_subject(detection_text)
                and not _CAUSAL_SOFTWARE_SUBJECT_RE.search(
                    _affirmed_evidence_projection(detection_text)
                )
                and not (
                    context_available
                    and _DIAGNOSTIC_CONTEXT_TOPICS.intersection(context_topics)
                )
            ):
                # A causal question remains meaningful outside software.
                # Specialize only its domain; do not discard the outer read
                # request after the debug rule won the same-verb competition.
                pattern_candidates = tuple(
                    replace(
                        candidate,
                        intent_type=IntentType.EXPLAIN,
                        action_type=ActionType.READ,
                    )
                    if candidate.intent_type is IntentType.DEBUG
                    and candidate.action_type is ActionType.ANALYZE
                    else candidate
                    for candidate in pattern_candidates
                )
            # A question owns one read request. Reuse its existing reading
            # candidate when present; embedded effect verbs are its subject,
            # not additional operations. Global offsets retain the role even
            # when clause splitting consumed the question mark.
            normalized_candidates = list(pattern_candidates)
            for frame_start, frame_end in question_spans:
                local_frame = (
                    max(frame_start, clause_span[0]) - clause_span[0],
                    min(frame_end, clause_span[1]) - clause_span[0],
                )
                if local_frame[0] >= local_frame[1]:
                    continue
                frame_candidates = [
                    candidate
                    for candidate in normalized_candidates
                    if local_frame[0] <= candidate.trigger_span[0]
                    and candidate.trigger_span[1] <= local_frame[1]
                    and not is_span_negated(matching_clause, candidate.trigger_span)
                ]
                reading_candidates = [
                    candidate for candidate in frame_candidates
                    if candidate.action_type in _READ_ONLY_CONTEXT_ACTIONS
                ]
                effect_candidates = [
                    candidate for candidate in frame_candidates
                    if candidate.action_type not in _READ_ONLY_CONTEXT_ACTIONS
                ]
                normalized_candidates = [
                    candidate for candidate in normalized_candidates
                    if candidate not in effect_candidates
                ]
                if reading_candidates:
                    continue
                if effect_candidates:
                    normalized_candidates.append(replace(
                        effect_candidates[0],
                        intent_type=IntentType.EXPLAIN,
                        action_type=ActionType.READ,
                    ))
                elif not matching_detection_text[
                    frame_start:clause_span[0]
                ].strip():
                    # A complete noun/wh question needs no imperative verb.
                    # Keep domain-free discussion statements for the model.
                    normalized_candidates.append(_PatternCandidate(
                        intent_type=IntentType.EXPLAIN,
                        action_type=ActionType.READ,
                        confidence=0.92,
                        match_span=local_frame,
                        trigger_span=local_frame,
                        pattern_order=-1,
                    ))
            pattern_candidates = tuple(sorted(
                normalized_candidates,
                key=lambda candidate: (
                    candidate.trigger_span[0], candidate.pattern_order,
                ),
            ))
            pattern_candidates = tuple(
                candidate
                for candidate in pattern_candidates
                if not _is_completed_action_mention(
                    matching_clause,
                    candidate.trigger_span,
                )
                and not _is_denied_prohibition_mention(
                    matching_clause,
                    candidate.trigger_span,
                )
                and not _is_discursive_double_negation_mention(
                    matching_clause,
                    candidate.trigger_span,
                )
                and not (
                    candidate.action_type is ActionType.RENAME
                    and _is_discussed_rename(
                        matching_clause,
                        candidate.trigger_span,
                    )
                )
                and not (
                    candidate.action_type
                    in {
                        ActionType.CREATE,
                        ActionType.UPDATE,
                        ActionType.DELETE,
                        ActionType.MOVE,
                        ActionType.MERGE,
                        ActionType.SPLIT,
                    }
                    and _is_discussed_mutation(
                        matching_clause,
                        candidate.trigger_span,
                    )
                )
                and not (
                    candidate.action_type is ActionType.EXECUTE
                    and _is_instructional_execution_mention(
                        matching_clause,
                        candidate.trigger_span,
                    )
                )
                and not (
                    candidate.action_type is ActionType.EXECUTE
                    and _is_reported_execution_mention(
                        matching_clause,
                        candidate.trigger_span,
                        following_text=detection_text[clause_span[1] :],
                    )
                )
                and not (
                    candidate.action_type is ActionType.EXECUTE
                    and _operation_trigger_is_reported_execution(
                        candidate.trigger_span,
                        clause_span,
                        reported_execution_spans,
                    )
                )
                and not (
                    candidate.action_type is ActionType.UPDATE
                    and _is_view_switch_change_mention(
                        matching_clause,
                        candidate.trigger_span,
                    )
                )
                and not (
                    candidate.intent_type is IntentType.DEBUG
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and _STRONG_SOFTWARE_SUBJECT_RE.search(matching_clause)
                    is None
                )
                and not (
                    candidate.intent_type is IntentType.TEST
                    and candidate.action_type is ActionType.EXECUTE
                    and _TEST_ARTIFACT_PRESERVATION_RE.search(
                        matching_clause
                    )
                )
                and not (
                    candidate.intent_type is IntentType.TEST
                    and candidate.action_type is ActionType.EXECUTE
                    and _is_nominal_test_execution_false_positive(
                        matching_clause,
                        candidate.trigger_span,
                    )
                )
                and not (
                    candidate.intent_type is IntentType.TEST
                    and candidate.action_type is ActionType.EXECUTE
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and not _has_affirmed_software_subject(matching_clause)
                    and not (
                        is_span_negated(
                            matching_clause,
                            candidate.trigger_span,
                        )
                        and _STRONG_SOFTWARE_SUBJECT_RE.search(
                            matching_clause
                        )
                    )
                )
                and not (
                    candidate.intent_type is IntentType.CODE
                    and candidate.action_type
                    in {ActionType.MOVE, ActionType.RENAME}
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and not _has_affirmed_software_subject(matching_clause)
                )
                and not (
                    candidate.intent_type is IntentType.REVIEW
                    and candidate.action_type
                    in {ActionType.ANALYZE, ActionType.COMPARE}
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and not _has_affirmed_software_subject(matching_clause)
                )
                and not (
                    candidate.intent_type is IntentType.CODE
                    and candidate.action_type is ActionType.RENAME
                    and _CONTEXT_ONLY_RENAME_RE.fullmatch(matching_clause)
                    and not is_span_negated(
                        matching_clause,
                        candidate.trigger_span,
                    )
                    and not context_supports_structural_rename
                )
            )
            detected: list[
                tuple[
                    IntentType,
                    ActionType,
                    float | None,
                    tuple[int, int] | None,
                    str,
                    tuple[int, int],
                ]
            ] = []
            if pattern_candidates:
                for index, candidate in enumerate(pattern_candidates):
                    operation_text, operation_span = self._operation_segment(
                        clause,
                        clause_span,
                        pattern_candidates,
                        index,
                    )
                    detected.append(
                        (
                            candidate.intent_type,
                            candidate.action_type,
                            candidate.confidence,
                            candidate.trigger_span,
                            operation_text,
                            operation_span,
                        )
                    )
            else:
                cue = self._match_vague_action(clause)
                if (
                    cue is not None
                    and operations
                    and operations[-1].polarity is IntentPolarity.AFFIRMED
                    and operations[-1].intent_type
                    in {IntentType.DEBUG, IntentType.REFACTOR}
                    and _TRAILING_PERFORMANCE_LOCATION_RE.fullmatch(
                        matching_clause
                    )
                ):
                    # ``profile startup, 看看慢在哪`` is one diagnosis.
                    # The colloquial ``看看`` qualifies the previous action; it
                    # is not an independent review request.
                    cue = None
                if (
                    cue is not None
                    and cue[1] not in _READ_ONLY_CONTEXT_ACTIONS
                    and not is_span_negated(matching_clause, cue[2])
                    and is_discussion_action(
                        matching_detection_text,
                        (clause_span[0] + cue[2][0], clause_span[0] + cue[2][1]),
                    )
                ):
                    cue = (IntentType.EXPLAIN, ActionType.READ, cue[2])
                if cue is not None and _is_completed_action_mention(
                    matching_clause,
                    cue[2],
                ):
                    cue = None
                if cue is not None and _is_denied_prohibition_mention(
                    matching_clause,
                    cue[2],
                ):
                    cue = None
                if cue is not None and _is_discursive_double_negation_mention(
                    matching_clause,
                    cue[2],
                ):
                    shared_leading_subject_keywords = self._extract_keywords(
                        matching_clause
                    )
                    cue = None
                if (
                    cue is not None
                    and cue[1] is ActionType.RENAME
                    and _is_discussed_rename(matching_clause, cue[2])
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[1]
                    in {
                        ActionType.CREATE,
                        ActionType.UPDATE,
                        ActionType.DELETE,
                        ActionType.MOVE,
                        ActionType.MERGE,
                        ActionType.SPLIT,
                    }
                    and _is_discussed_mutation(matching_clause, cue[2])
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[1] is ActionType.EXECUTE
                    and _is_instructional_execution_mention(
                        matching_clause,
                        cue[2],
                    )
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[1] is ActionType.EXECUTE
                    and _is_reported_execution_mention(
                        matching_clause,
                        cue[2],
                        following_text=detection_text[clause_span[1] :],
                    )
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[1] is ActionType.EXECUTE
                    and _operation_trigger_is_reported_execution(
                        cue[2],
                        clause_span,
                        reported_execution_spans,
                    )
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[1] is ActionType.UPDATE
                    and _is_view_switch_change_mention(
                        matching_clause,
                        cue[2],
                    )
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.DEBUG
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and _STRONG_SOFTWARE_SUBJECT_RE.search(matching_clause)
                    is None
                ):
                    # Startup/latency wording also describes cars and
                    # appliances.  A debugging operation needs an independent
                    # software-shaped subject before it can grant engineering
                    # routing authority.
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.TEST
                    and cue[1] is ActionType.EXECUTE
                    and _TEST_ARTIFACT_PRESERVATION_RE.search(
                        matching_clause
                    )
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.TEST
                    and cue[1] is ActionType.EXECUTE
                    and _is_nominal_test_execution_false_positive(
                        matching_clause,
                        cue[2],
                    )
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.TEST
                    and cue[1] is ActionType.EXECUTE
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and not _has_affirmed_software_subject(matching_clause)
                ):
                    # ``run`` and ``跑`` are ordinary movement verbs too.
                    # A lifestyle subject cannot authorize a test runner
                    # without a separate software/test-shaped target.
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.CODE
                    and cue[1] in {ActionType.MOVE, ActionType.RENAME}
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and not _has_affirmed_software_subject(matching_clause)
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.REVIEW
                    and cue[1] in {ActionType.ANALYZE, ActionType.COMPARE}
                    and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                        matching_clause
                    )
                    and not _has_affirmed_software_subject(matching_clause)
                ):
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.EXPLAIN
                    and operations
                    and operations[-1].polarity is IntentPolarity.AFFIRMED
                    and operations[-1].intent_type is IntentType.EXPLAIN
                    and operations[-1].action_type is ActionType.READ
                    and _TRAILING_EXPLAIN_QUALIFIER_RE.fullmatch(
                        matching_clause
                    )
                ):
                    # A trailing ``只解释`` qualifies the explanation that
                    # immediately precedes it; it is not a second operation.
                    cue = None
                if (
                    cue is not None
                    and cue[0] is IntentType.CODE
                    and cue[1] is ActionType.RENAME
                    and _CONTEXT_ONLY_RENAME_RE.fullmatch(matching_clause)
                    and not is_span_negated(matching_clause, cue[2])
                    and not context_supports_structural_rename
                ):
                    cue = None
                if (
                    reported_bug_subject
                    and cue is not None
                    and cue[1] is ActionType.DELETE
                ):
                    # In "user-reported deletion bug", deletion names the
                    # defect; it is not authority to delete anything.
                    cue = None
                if cue is None:
                    if (
                        clause_index < len(clauses) - 1
                        and _HYPOTHETICAL_RENAME_PREFIX_RE.search(matching_clause)
                        and _TECHNICAL_MARKER_RE.search(matching_clause)
                    ):
                        # A hypothetical file event can ground a following
                        # explanation, but cannot provide a missing target for
                        # a subsequent effectful command.
                        pending_subject = (clause, clause_span)
                        pending_subject_read_only = True
                        continue
                    if (
                        clause_index < len(clauses) - 1
                        and any(
                            report_start < clause_span[1]
                            and clause_span[0] < report_end
                            for report_start, report_end in reported_execution_spans
                        )
                        and re.search(
                            r"(?<![a-z0-9_])(?:ci|pytest|pyetst|tests?)"
                            r"(?![a-z0-9_])|(?:测试|单测|用例)",
                            matching_clause,
                            re.IGNORECASE,
                        )
                    ):
                        # A closed attempt grants no execution authority, but
                        # its runner can resolve a later explicit deictic
                        # command in the same turn: ``Unable to run pytest;
                        # please run it now``.
                        pending_subject = (clause, clause_span)
                        continue
                    if (
                        clause_index == len(clauses) - 1
                        and operations
                        and coarse_polarity is IntentPolarity.AFFIRMED
                        and operations[-1].polarity is IntentPolarity.AFFIRMED
                        and operations[-1].intent_type
                        in {IntentType.DEBUG, IntentType.REFACTOR}
                        and _PERFORMANCE_SYMPTOM_RE.search(matching_clause)
                    ):
                        # A final actionless clause often supplies evidence for
                        # the preceding request: ``refactor the query; it is
                        # doing a full table scan``.  Keep that detail on the
                        # nearest diagnostic/optimization operation instead of
                        # broadcasting whole-turn performance words to every
                        # operation lane.
                        previous = operations[-1]
                        previous_span = previous.span
                        if (
                            previous_span is not None
                            and previous_span[1] <= clause_span[0]
                        ):
                            detail_code = "rule:trailing-performance-detail"
                            merged_entities = self._merge_entities(
                                {
                                    key: list(values)
                                    for key, values in previous.entities.items()
                                },
                                self._extract_entities(clause),
                            )
                            operations[-1] = replace(
                                previous,
                                clause=text[previous_span[0] : clause_span[1]],
                                span=(previous_span[0], clause_span[1]),
                                entities={
                                    key: tuple(values)
                                    for key, values in merged_entities.items()
                                },
                                keywords=tuple(
                                    self._stable_values(
                                        [
                                            *previous.keywords,
                                            *self._extract_keywords(clause),
                                        ]
                                    )
                                ),
                                evidence_codes=(
                                    *previous.evidence_codes,
                                    detail_code,
                                ),
                            )
                            evidence.append(
                                IntentEvidence(
                                    code=detail_code,
                                    source=EvidenceSource.RULE,
                                    reason=(
                                        "a trailing diagnostic clause qualifies "
                                        "the preceding operation"
                                    ),
                                    strength=0.72,
                                    span=clause_span,
                                )
                            )
                            continue
                    if (
                        coarse_polarity is IntentPolarity.AFFIRMED
                        and (
                            (
                                _TECHNICAL_MARKER_RE.search(matching_clause)
                                and _PENDING_SUBJECT_RE.search(matching_clause)
                            )
                            or reported_bug_subject
                        )
                        and (
                            not ACTION_MARKER_RE.search(matching_clause)
                            or _PENDING_TEST_FAILURE_RE.search(matching_clause)
                            or reported_bug_subject
                        )
                    ):
                        pending_subject = (clause, clause_span)
                    continue
                intent_type, action_type, trigger_span = cue
                scope_start = scope_start_for_span(matching_clause, trigger_span)
                raw_operation_text = clause[scope_start:]
                operation_text = raw_operation_text.strip()
                local_start = (
                    scope_start
                    + len(raw_operation_text)
                    - len(raw_operation_text.lstrip())
                )
                detected.append(
                    (
                        intent_type,
                        action_type,
                        None,
                        trigger_span,
                        operation_text,
                        (
                            clause_span[0] + local_start,
                            clause_span[0] + local_start + len(operation_text),
                        ),
                    )
                )

            if pending_subject is not None and detected:
                subject_text, subject_span = pending_subject
                gap = text[subject_span[1] : clause_span[0]]
                pending_is_execution_report = any(
                    report_start < subject_span[1]
                    and subject_span[0] < report_end
                    for report_start, report_end in reported_execution_spans
                )
                first_detected = detected[0]
                may_resolve_reported_runner = bool(
                    pending_is_execution_report
                    and first_detected[1] is ActionType.EXECUTE
                    and _REFERENCE_RE.search(str(first_detected[4]))
                )
                if (
                    (
                        not re.search(r"[.!?;\n。！？；]", gap)
                        or may_resolve_reported_runner
                    )
                    and (
                        not pending_subject_read_only
                        or first_detected[1] in _READ_ONLY_CONTEXT_ACTIONS
                    )
                ):
                    first = list(detected[0])
                    merged_span = (subject_span[0], first[5][1])
                    first[4] = text[merged_span[0] : merged_span[1]]
                    first[5] = merged_span
                    if (
                        first[0]
                        in {IntentType.SEARCH, IntentType.EXPLAIN, IntentType.REVIEW}
                        and first[1] in {ActionType.READ, ActionType.ANALYZE}
                        and _PENDING_SUBJECT_RE.search(subject_text)
                        and (
                            _ROOT_CAUSE_RE.search(str(first[4]))
                            or (
                                _CONCURRENCY_PROBLEM_RE.search(subject_text)
                                and _CONCURRENCY_ANALYSIS_RE.search(str(first[4]))
                            )
                        )
                    ):
                        first[0] = IntentType.DEBUG
                        first[1] = ActionType.ANALYZE
                    detected[0] = tuple(first)  # type: ignore[assignment]
                pending_subject = None
                pending_subject_read_only = False

            for (
                intent_type,
                action_type,
                match_confidence,
                trigger_span,
                operation_text,
                operation_span,
            ) in detected:
                previous_operation = (
                    previous_intent.primary_operation
                    if previous_intent is not None
                    else None
                )
                trigger_is_negated = bool(
                    trigger_span is not None
                    and (
                        is_span_negated(matching_clause, trigger_span)
                        or (
                            clause_context_denied
                            and clause_context_match is not None
                            and trigger_span[0] >= clause_context_match.start()
                        )
                        or _TRAILING_ACTION_REVOCATION_RE.search(
                            matching_clause[trigger_span[1] :]
                        )
                        or (
                            intent_type is IntentType.TEST
                            and action_type is ActionType.CREATE
                            and _has_effective_test_denial(
                                _TEST_CREATION_DENIAL_RE,
                                matching_clause,
                            )
                        )
                        or (
                            intent_type is IntentType.TEST
                            and action_type is ActionType.EXECUTE
                            and _has_effective_test_denial(
                                _TEST_EXECUTION_DENIAL_RE,
                                matching_clause,
                            )
                        )
                    )
                )
                scoped_operation_matching_text = (
                    _mask_protected_intent_literals(operation_text)
                )
                operation_matching_text = (
                    scoped_operation_matching_text
                    if trigger_is_negated
                    else _affirmed_evidence_projection(
                        scoped_operation_matching_text
                    )
                )
                protected_literal_spans = _protected_intent_literal_spans(
                    operation_text
                )
                protected_literal_file_targets = (
                    _isolated_protected_file_targets(operation_text)
                )
                protected_literal_file_target_values = frozenset(
                    target.casefold()
                    for target in protected_literal_file_targets
                )
                protected_literal_operation_targets = (
                    tuple(
                        dict.fromkeys(
                            (
                                *protected_literal_file_targets,
                                *_read_only_protected_file_targets(
                                    operation_text
                                ),
                            )
                        )
                    )
                    if action_type in _READ_ONLY_CONTEXT_ACTIONS
                    else (
                        protected_literal_file_targets
                        if (
                            action_type is ActionType.EXECUTE
                            and _DIRECT_PROTECTED_TEST_FILE_TARGET_RE.search(
                                operation_text
                            )
                            is not None
                        )
                        else ()
                    )
                )
                protected_literal_operation_target_values = frozenset(
                    target.casefold()
                    for target in protected_literal_operation_targets
                )
                protected_file_targets = tuple(
                    dict.fromkeys(
                        match.group("target")
                        for match in _INLINE_CODE_FILE_ACTION_TARGET_RE.finditer(
                            operation_text
                        )
                    )
                )
                protected_file_target_values = frozenset(
                    target.casefold() for target in protected_file_targets
                )
                operation_has_explicit_technical_marker = bool(
                    _TECHNICAL_MARKER_RE.search(operation_matching_text)
                    or protected_file_targets
                    or protected_literal_operation_targets
                )
                operation_has_technical_topic = bool(
                    operation_has_explicit_technical_marker
                    or _PENDING_SUBJECT_RE.search(operation_matching_text)
                    or _REPORTED_FAILURE_CONTEXT_RE.search(
                        operation_matching_text
                    )
                )
                operation_has_distinct_topic = bool(
                    protected_file_targets
                    or protected_literal_operation_targets
                    or _has_explicit_operation_topic(operation_matching_text)
                )
                visible_operation_text = scoped_operation_matching_text.casefold()
                visible_operation_targets = frozenset(
                    target
                    for target in _context_entity_values(
                        self._extract_entities(operation_text)
                    )
                    if target in visible_operation_text
                )
                operation_target_values = (
                    visible_operation_targets
                    | protected_file_target_values
                    | protected_literal_operation_target_values
                )
                unsafe_context_referential_effect = bool(
                    unsafe_previous_context
                    and not trigger_is_negated
                    and action_type not in _READ_ONLY_CONTEXT_ACTIONS
                    and not operation_target_values
                    and not operation_has_distinct_topic
                    and (
                        unsafe_effectful_context_cue
                        or _REFERENCE_RE.search(operation_matching_text)
                        or _ANAPHORIC_OPERATION_TOPIC_RE.search(
                            operation_matching_text
                        )
                        or _PRIOR_RESULT_REFERENCE_RE.search(operation_text)
                        or (
                            operations
                            and not operation_has_distinct_topic
                            and any(
                                code.startswith("context:")
                                for code in operations[-1].evidence_codes
                            )
                        )
                    )
                )
                conflicting_context_referential_effect = bool(
                    context_frame_conflict
                    and not trigger_is_negated
                    and action_type not in _READ_ONLY_CONTEXT_ACTIONS
                    and not operation_target_values
                    and _REFERENCE_RE.search(operation_matching_text)
                )
                if (
                    unsafe_context_referential_effect
                    or conflicting_context_referential_effect
                ):
                    # The current verb is effectful but its object exists only
                    # in a destructive/execute/structural prior turn.  Keeping
                    # it would manufacture fresh authority from history (for
                    # example DELETE -> ``optimize the bottleneck we found``).
                    # An explicit present-turn target remains independently
                    # actionable and is deliberately not filtered here.
                    continue
                operation_allows_unsafe_read_only_context = bool(
                    unsafe_previous_context
                    and not context_frame_conflict
                    and not trigger_is_negated
                    and action_type in _READ_ONLY_CONTEXT_ACTIONS
                    and _REFERENCE_RE.search(operation_matching_text)
                    and not current_turn_has_new_context_target
                    and not current_turn_denies_context_target
                    and not operation_has_distinct_topic
                )
                operation_context_reuse_blocked = bool(
                    context_reuse_blocked
                    and not operation_allows_unsafe_read_only_context
                )
                intra_turn_continuation_target = bool(
                    operations
                    and _context_entity_values(operations[-1].entities)
                    and not trigger_is_negated
                    and action_type in _READ_ONLY_CONTEXT_ACTIONS
                    and not operation_target_values
                    and clause_context_match is not None
                    and not _context_denies_prior_target(
                        detection_text,
                        (
                            clause_span[0] + clause_context_match.start(),
                            clause_span[0] + clause_context_match.end(),
                        ),
                        operations[-1].entities,
                    )
                )
                effective_operation_target_values = operation_target_values
                if intra_turn_continuation_target:
                    effective_operation_target_values |= _context_entity_values(
                        operations[-1].entities
                    )
                operation_reuses_context_target = bool(
                    effective_operation_target_values
                    and effective_operation_target_values.issubset(
                        _context_entity_values(context_entities)
                    )
                )
                local_discourse_reference = bool(
                    operations
                    and _LOCAL_DISCOURSE_REFERENCE_RE.search(
                        operation_matching_text
                    )
                )
                contextual_test_execution = bool(
                    not trigger_is_negated
                    and action_type is ActionType.EXECUTE
                    and context_cue is not None
                    and previous_operation is not None
                    and _REFERENCE_RE.search(operation_matching_text)
                    and _TEST_CONTEXT_TOPICS.intersection(
                        topic.lower() for topic in context_topics
                    )
                )
                contextual_diagnostic_probe = bool(
                    not trigger_is_negated
                    and context_cue is not None
                    and context_cue.read_only_probe
                    and (
                        (intent_type, action_type)
                        == (IntentType.SEARCH, ActionType.READ)
                        or (intent_type, action_type)
                        == (IntentType.REVIEW, ActionType.ANALYZE)
                    )
                    and (
                        not operation_has_distinct_topic
                        or operation_reuses_context_target
                    )
                    and not local_discourse_reference
                    and (
                        (
                            previous_operation is not None
                            and
                            previous_operation.intent_type is IntentType.DEBUG
                            and previous_operation.action_type
                            in _READ_ONLY_CONTEXT_ACTIONS
                        )
                        or bool(
                            _TEST_CONTEXT_TOPICS.intersection(
                                topic.lower() for topic in context_topics
                            )
                        )
                    )
                )
                contextual_read_only_pair = (
                    _read_only_probe_pair(
                        previous_operation,
                        context_topics,
                        text,
                    )
                    if (
                        not trigger_is_negated
                        and context_available
                        and context_cue is not None
                        and context_cue.read_only_probe
                        and action_type in _READ_ONLY_CONTEXT_ACTIONS
                        and (
                            not operation_has_distinct_topic
                            or operation_reuses_context_target
                        )
                        and not local_discourse_reference
                    )
                    else None
                )
                contextual_update_intent = (
                    _context_update_intent(context_topics)
                    if (
                        not trigger_is_negated
                        and context_available
                        and not context_reuse_blocked
                        and context_cue is not None
                        and action_type is ActionType.UPDATE
                        and not operation_has_distinct_topic
                    )
                    else None
                )
                protected_literal_reference = bool(
                    action_type
                    in {
                        ActionType.READ,
                        ActionType.ANALYZE,
                        ActionType.COMPARE,
                    }
                    and (
                        (
                            protected_literal_spans
                            and not (
                                protected_literal_file_target_values
                                and protected_literal_file_target_values.issubset(
                                    _context_entity_values(context_entities)
                                )
                            )
                        )
                        or (
                            text_has_protected_literal
                            and _REFERENCE_RE.search(operation_matching_text)
                            and not (
                                protected_literal_file_target_values
                                and protected_literal_file_target_values.issubset(
                                    _context_entity_values(context_entities)
                                )
                            )
                        )
                    )
                )
                if (
                    not trigger_is_negated
                    and intent_type is IntentType.CODE
                    and action_type is ActionType.UPDATE
                    and not context_reuse_blocked
                    and context_cue is not None
                    and performance_context_continuation is not None
                    and _PERFORMANCE_SIGNAL_CONTEXT_TOPICS.intersection(
                        topic.lower() for topic in context_topics
                    )
                    and _CONTEXT_PERFORMANCE_TARGET_RE.search(
                        operation_matching_text
                    )
                ):
                    # The current turn already grants the update.  Context
                    # narrows only its domain (a slow-query continuation),
                    # never its action or polarity.
                    intent_type = IntentType.REFACTOR
                elif contextual_test_execution:
                    # The current turn explicitly grants execution authority;
                    # test-shaped context resolves only what ``it`` denotes.
                    # Do not inherit the prior inspect/analyze action.
                    intent_type = IntentType.TEST
                elif contextual_read_only_pair is not None:
                    # The current probe supplies read-only authority. Context
                    # chooses only the domain and cannot replay a previous
                    # execute, create, delete, or mutation action.
                    intent_type, action_type = contextual_read_only_pair
                elif contextual_diagnostic_probe:
                    # ``接着定位`` is a present-turn read-only action.  In a
                    # verified diagnostic/test context, specialize its domain
                    # without copying any prior mutation or execution action.
                    intent_type = IntentType.DEBUG
                    action_type = ActionType.ANALYZE
                elif contextual_update_intent is not None:
                    # ``fix/change it`` already grants UPDATE.  A bounded
                    # active topic may distinguish docs/frontend without
                    # changing the action or its polarity.
                    intent_type = contextual_update_intent
                contextual_domain_inheritance = False
                if (
                    match_confidence is None
                    and not trigger_is_negated
                    and previous_operation is not None
                    and context_available
                    and not operation_context_reuse_blocked
                    and not current_turn_has_new_context_target
                    and not current_turn_denies_context_target
                    and not operation_has_distinct_topic
                    and not protected_literal_reference
                    and not contextual_test_execution
                    and contextual_read_only_pair is None
                    and contextual_update_intent is None
                    and not local_discourse_reference
                    and (
                        (
                            context_cue is not None
                            and not context_cue.read_only_probe
                        )
                        or (
                            context_cue is None
                            and _REFERENCE_RE.search(operation_matching_text)
                        )
                    )
                ):
                    # The present turn owns the action and its authority.
                    # Context may refine only the domain when that domain
                    # supports the action that was actually requested.  Never
                    # copy a historical action: doing so turns ``inspect it``
                    # into a prior create/delete/execute operation.
                    supported_actions = SEMANTIC_INTENT_ALLOWED_ACTIONS.get(
                        previous_operation.intent_type,
                        frozenset(),
                    )
                    if action_type in supported_actions:
                        contextual_domain_inheritance = bool(
                            intent_type is not previous_operation.intent_type
                        )
                        intent_type = previous_operation.intent_type
                elif (
                    not trigger_is_negated
                    and operations
                    and action_type is ActionType.UPDATE
                    and operations[-1].action_type is ActionType.UPDATE
                    and _PRIOR_RESULT_REFERENCE_RE.search(operation_text)
                ):
                    # A corrective continuation can refer to the result of the
                    # previous operation rather than introduce a new bug lane:
                    # e.g. run a linter and fix what it reports.
                    intent_type = operations[-1].intent_type
                elif (
                    match_confidence is None
                    and not trigger_is_negated
                    and operations
                    and intent_type is IntentType.CODE
                    and action_type is operations[-1].action_type
                ):
                    # A generic continuation such as "并给出索引改法" is the
                    # second half of the current refactor, not a new generic
                    # coding job. Distinct actions remain separate.
                    intent_type = operations[-1].intent_type
                polarity = (
                    IntentPolarity.NEGATED
                    if trigger_is_negated
                    else (
                        coarse_polarity
                        if trigger_span is None
                        else IntentPolarity.AFFIRMED
                    )
                )
                if (
                    polarity is IntentPolarity.NEGATED
                    and action_type is ActionType.UPDATE
                    and re.search(
                        r"\b(?:fix|repair)\b", operation_text, re.IGNORECASE
                    )
                    and not any(
                        operation.intent_type is IntentType.DEBUG
                        for operation in operations
                    )
                    and _PENDING_SUBJECT_RE.search(operation_text) is None
                    and _REPORTED_FAILURE_CONTEXT_RE.search(detection_text) is None
                ):
                    intent_type = IntentType.CODE
                source = EvidenceSource.RULE
                reason_code = ""
                used_context_for_clause = bool(
                    contextual_domain_inheritance
                    or contextual_read_only_pair is not None
                    or contextual_update_intent is not None
                )
                explicit_bare_read_only = bool(
                    action_type in _READ_ONLY_CONTEXT_ACTIONS
                    and _EXPLICIT_BARE_READ_ONLY_RE.fullmatch(
                        scoped_operation_matching_text
                    )
                )
                if match_confidence is not None:
                    confidence = self._direct_confidence(
                        match_confidence, self._extract_entities(operation_text)
                    )
                    reason_code = f"rule:{intent_type.value}.{action_type.value}"
                else:
                    confidence = 0.0
                    has_current_topic = (
                        operation_has_technical_topic
                        or protected_literal_reference
                    )
                    unsafe_context_inference = (
                        polarity is IntentPolarity.AFFIRMED
                        and action_type in _UNSAFE_CONTEXT_ACTIONS
                        and not has_current_topic
                        and not contextual_test_execution
                    )
                    if polarity is IntentPolarity.NEGATED:
                        confidence = 0.94
                        reason_code = (
                            f"explicit:negated-{intent_type.value}.{action_type.value}"
                        )
                        source = EvidenceSource.EXPLICIT
                    elif contextual_domain_inheritance:
                        confidence = 0.72
                        reason_code = (
                            f"context:resolved-{intent_type.value}."
                            f"{action_type.value}"
                        )
                        source = EvidenceSource.CONTEXT
                    elif has_current_topic:
                        confidence = 0.84
                        reason_code = (
                            f"rule:action-cue-{intent_type.value}.{action_type.value}"
                        )
                    elif explicit_bare_read_only:
                        # A fully bounded read-only imperative is useful intent
                        # even when the user intentionally omits its object.
                        # It grants no write/execute authority and leaves target
                        # resolution to the caller or a later clarification.
                        confidence = 0.7
                        reason_code = (
                            f"rule:bare-read-only-{intent_type.value}."
                            f"{action_type.value}"
                        )
                    elif operations:
                        # The action is still explicit in the current turn; an
                        # earlier clause only supplies the omitted target.
                        confidence = 0.78
                        reason_code = (
                            f"rule:intra-turn-{intent_type.value}.{action_type.value}"
                        )
                    elif (
                        context_available
                        and not operation_context_reuse_blocked
                        and not operation_has_distinct_topic
                        and not unsafe_context_inference
                    ):
                        confidence = 0.68
                        reason_code = (
                            f"context:resolved-{intent_type.value}.{action_type.value}"
                        )
                        source = EvidenceSource.CONTEXT
                        used_context_for_clause = True
                    else:
                        continue

                if polarity is IntentPolarity.NEGATED:
                    confidence = max(confidence, 0.94)
                    if not reason_code.startswith("explicit:"):
                        reason_code = (
                            f"explicit:negated-{intent_type.value}.{action_type.value}"
                        )
                        source = EvidenceSource.EXPLICIT

                clause_entities = self._extract_entities(operation_text)
                for target in (
                    *protected_file_targets,
                    *protected_literal_operation_targets,
                ):
                    if target not in clause_entities["files"]:
                        clause_entities["files"].append(target)
                if action_type in _READ_ONLY_CONTEXT_ACTIONS:
                    excluded_view_targets = {
                        target.casefold()
                        for match in _VIEW_SWITCH_EXCLUDED_FILE_RE.finditer(
                            operation_text
                        )
                        for target in match.groupdict().values()
                        if target
                    }
                    if excluded_view_targets:
                        clause_entities["files"] = [
                            target
                            for target in clause_entities["files"]
                            if target.casefold() not in excluded_view_targets
                        ]
                clause_keywords = self._extract_keywords(operation_text)
                inherit_intra_turn_keywords = bool(
                    operations
                    and not operation_has_distinct_topic
                )
                if inherit_intra_turn_keywords:
                    clause_keywords = self._stable_values(
                        [*operations[-1].keywords, *clause_keywords]
                    )
                if polarity is IntentPolarity.NEGATED:
                    negators = effective_negation_matches(operation_matching_text)
                    if negators:
                        neutral_prefix = operation_text[: negators[0].start()]
                        if (
                            _TECHNICAL_MARKER_RE.search(neutral_prefix)
                            or _PENDING_SUBJECT_RE.search(neutral_prefix)
                            or re.search(
                                r"\b(?:bug|issue|error|failure)\b",
                                neutral_prefix,
                                re.IGNORECASE,
                            )
                        ):
                            shared_leading_subject_keywords = (
                                self._extract_keywords(neutral_prefix)
                            )
                elif (
                    shared_leading_subject_keywords
                    and not operation_has_explicit_technical_marker
                    and not any(clause_entities.values())
                ):
                    clause_keywords = self._stable_values(
                        [*shared_leading_subject_keywords, *clause_keywords]
                    )
                if (
                    intent_type is IntentType.CODE
                    and action_type is ActionType.CREATE
                    and _IMPLEMENTATION_WITH_TESTS_RE.search(matching_clause)
                ):
                    # ``with tests`` qualifies the implementation as well as
                    # requesting a test deliverable. Preserve that shared topic
                    # without broadcasting arbitrary compound-clause keywords.
                    clause_keywords = self._stable_values(
                        [*clause_keywords, *self._extract_keywords(clause)]
                    )
                current_reference_terms = self._specific_reference_terms(
                    clause_keywords
                )
                if context_cue is not None and context_cue.read_only_probe:
                    current_reference_terms = frozenset(
                        current_reference_terms
                        - _CONTEXT_FACET_REFERENCE_TERMS
                    )
                shared_reference_terms = (
                    current_reference_terms & context_reference_terms
                )
                normalized_clause_keywords = {
                    keyword.lower() for keyword in clause_keywords
                }
                shared_safe_topics = normalized_clause_keywords.intersection(
                    context_topics
                )
                explicit_continuation = bool(
                    context_cue is not None
                    or _CONTINUATION_RE.search(operation_matching_text)
                    or _CONTEXT_REUSE_RE.search(operation_matching_text)
                )
                contextual_output_reference = bool(
                    intent_type is IntentType.DOCUMENT
                    and action_type in {ActionType.CREATE, ActionType.UPDATE}
                    and _REFERENCE_RE.search(operation_matching_text)
                )
                clause_target_values = _context_entity_values(clause_entities)
                context_target_values = _context_entity_values(context_entities)
                same_context_target = bool(
                    clause_target_values
                    and clause_target_values.issubset(context_target_values)
                )
                contextual_topic_reference = bool(
                    polarity is IntentPolarity.AFFIRMED
                    and context_available
                    and not operation_context_reuse_blocked
                    and (
                        action_type not in _UNSAFE_CONTEXT_ACTIONS
                        or match_confidence is not None
                    )
                    and not protected_literal_reference
                    and (
                        not any(clause_entities.values())
                        or same_context_target
                    )
                    and (
                        shared_reference_terms
                        or contextual_output_reference
                        or (
                            _REFERENCE_RE.search(operation_matching_text)
                            and not current_reference_terms
                        )
                        or (
                            explicit_continuation
                            and not current_reference_terms
                            and (
                                shared_safe_topics
                                or context_cue is not None
                            )
                        )
                    )
                )
                intra_turn_reference = bool(
                    operations
                    and (
                        intra_turn_continuation_target
                        or _REFERENCE_RE.search(operation_text)
                        or (
                            match_confidence is None
                            and not operation_has_distinct_topic
                        )
                    )
                )
                if intra_turn_reference:
                    clause_entities = self._merge_entities(
                        clause_entities,
                        operations[-1].entities,
                    )
                if (
                    context_available
                    and not operation_context_reuse_blocked
                    and (
                        not any(clause_entities.values())
                        or same_context_target
                    )
                    and not local_discourse_reference
                    and (
                        not operation_has_distinct_topic
                        or contextual_topic_reference
                    )
                    and (
                        used_context_for_clause
                        or bool(
                            not protected_literal_reference
                            and _REFERENCE_RE.search(operation_matching_text)
                        )
                        or contextual_topic_reference
                    )
                ):
                    clause_entities = self._merge_entities(
                        clause_entities, context_entities
                    )
                    used_context_for_clause = bool(
                        used_context_for_clause
                        or context_entities
                        or (context_cue is not None and context_topics)
                        or contextual_topic_reference
                    )

                codes = [reason_code]
                evidence.append(
                    IntentEvidence(
                        code=reason_code,
                        source=source,
                        reason=self._evidence_reason(source, polarity),
                        strength=round(confidence, 3),
                        span=operation_span,
                    )
                )
                if (
                    polarity is IntentPolarity.NEGATED
                    and source is not EvidenceSource.EXPLICIT
                ):
                    codes.append("explicit:negation-scope")
                    evidence.append(
                        IntentEvidence(
                            code="explicit:negation-scope",
                            source=EvidenceSource.EXPLICIT,
                            reason="current clause explicitly negates the operation",
                            strength=0.98,
                            span=operation_span,
                        )
                    )
                if used_context_for_clause:
                    context_used = True
                    if not any(code.startswith("context:") for code in codes):
                        context_code = (
                            "context:topic-continuation"
                            if contextual_topic_reference
                            else "context:entity-resolution"
                        )
                        codes.append(context_code)
                        evidence.append(
                            IntentEvidence(
                                code=context_code,
                                source=EvidenceSource.CONTEXT,
                                reason=(
                                    "current operation continues the active task topic"
                                    if contextual_topic_reference
                                    else "recent context resolves the referenced topic"
                                ),
                                strength=0.62,
                                span=operation_span,
                                turn_offset=-1,
                            )
                        )
                if intra_turn_reference:
                    clause_keywords = self._stable_values(
                        [*clause_keywords, *operations[-1].keywords]
                    )
                if used_context_for_clause:
                    inherited_topics = list(context_topics)
                    if contextual_topic_reference and not current_reference_terms:
                        inherited_topics.extend(sorted(context_reference_terms))
                    for topic in inherited_topics:
                        if topic not in clause_keywords:
                            clause_keywords.append(topic)
                operation = IntentOperation(
                        intent_type=intent_type,
                        action_type=action_type,
                        polarity=polarity,
                        confidence=round(min(confidence, 1.0), 3),
                        clause=operation_text,
                        span=operation_span,
                        entities={
                            key: tuple(values)
                            for key, values in clause_entities.items()
                        },
                        keywords=tuple(clause_keywords),
                        evidence_codes=tuple(codes),
                    )
                if operations:
                    merged = self._coalesce_adjacent_operation(
                        operations[-1],
                        operation,
                        text,
                    )
                    if merged is not None:
                        operations[-1] = merged
                        continue
                operations.append(operation)

        if retraction is not None:
            active_text = text[retraction.active_start :]
            retracted_dependency_change = any(
                operation.intent_type is IntentType.CONFIGURE
                and operation.action_type is ActionType.UPDATE
                and operation.polarity is IntentPolarity.NEGATED
                and operation.span is not None
                and operation.span[1] <= retraction.superseded_end
                and any(
                    code.startswith("explicit:retracted-")
                    for code in operation.evidence_codes
                )
                and {
                    keyword.lower() for keyword in operation.keywords
                }.intersection(
                    {"dependency", "package", "requirements", "version"}
                )
                for operation in operations
            )
            if (
                retracted_dependency_change
                and _DEPENDENCY_PIN_REVIEW_RE.search(active_text)
                and _NON_DEPENDENCY_PIN_CONTEXT_RE.search(active_text) is None
            ):
                for index, operation in enumerate(operations):
                    if (
                        operation.intent_type is IntentType.REVIEW
                        and operation.action_type is ActionType.ANALYZE
                        and operation.polarity is IntentPolarity.AFFIRMED
                        and operation.span is not None
                        and operation.span[0] >= retraction.active_start
                    ):
                        operations[index] = replace(
                            operation,
                            keywords=tuple(
                                self._stable_values(
                                    [
                                        *operation.keywords,
                                        "dependency",
                                        "package",
                                        "version",
                                        "dependency pin",
                                    ]
                                )
                            ),
                        )
            active_affirmed_pairs = {
                (operation.intent_type, operation.action_type)
                for operation in operations
                if operation.polarity is IntentPolarity.AFFIRMED
                and operation.span is not None
                and operation.span[0] >= retraction.active_start
            }
            # A correction to an unrelated request supersedes an earlier test
            # execution completely: ``run pytest; scratch that—review auth``
            # is just a review request, not a standing test prohibition.  Keep
            # the denial when the replacement still discusses the test/run or
            # its output (for example, ``inspect the traceback only``), since
            # that restriction remains relevant to downstream routing.
            unrelated_execution_denials = [
                operation
                for operation in operations
                if operation.polarity is IntentPolarity.NEGATED
                and operation.action_type is ActionType.EXECUTE
                and operation.span is not None
                and operation.span[1] <= retraction.superseded_end
                and any(
                    code.startswith("explicit:retracted-")
                    for code in operation.evidence_codes
                )
                and active_affirmed_pairs
                and _RETRACTED_EXECUTION_TOPIC_RE.search(active_text) is None
            ]
            if unrelated_execution_denials:
                removed_ids = {
                    id(operation) for operation in unrelated_execution_denials
                }
                removed_codes = {
                    code
                    for operation in unrelated_execution_denials
                    for code in operation.evidence_codes
                }
                operations = [
                    operation
                    for operation in operations
                    if id(operation) not in removed_ids
                ]
                retained_codes = {
                    code
                    for operation in operations
                    for code in operation.evidence_codes
                }
                evidence = [
                    item
                    for item in evidence
                    if item.code not in removed_codes
                    or item.code in retained_codes
                ]
            superseded_denials = [
                operation
                for operation in operations
                if operation.polarity is IntentPolarity.NEGATED
                and operation.action_type
                in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
                and (operation.intent_type, operation.action_type)
                in active_affirmed_pairs
                and operation.span is not None
                and operation.span[1] <= retraction.superseded_end
            ]
            if superseded_denials:
                if _CORRECTION_TOPIC_REUSE_RE.search(
                    text[retraction.active_start :]
                ):
                    for denial in superseded_denials:
                        for index, active in enumerate(operations):
                            if (
                                active.polarity is IntentPolarity.AFFIRMED
                                and active.intent_type is denial.intent_type
                                and active.action_type is denial.action_type
                                and active.span is not None
                                and active.span[0] >= retraction.active_start
                            ):
                                operations[index] = replace(
                                    active,
                                    keywords=tuple(
                                        self._stable_values(
                                            [*active.keywords, *denial.keywords]
                                        )
                                    ),
                                    entities={
                                        key: tuple(values)
                                        for key, values in self._merge_entities(
                                            {
                                                key: list(values)
                                                for key, values in active.entities.items()
                                            },
                                            denial.entities,
                                        ).items()
                                    },
                                )
                removed_ids = {id(operation) for operation in superseded_denials}
                removed_codes = {
                    code
                    for operation in superseded_denials
                    for code in operation.evidence_codes
                }
                operations = [
                    operation
                    for operation in operations
                    if id(operation) not in removed_ids
                ]
                retained_codes = {
                    code
                    for operation in operations
                    for code in operation.evidence_codes
                }
                evidence = [
                    item
                    for item in evidence
                    if item.code not in removed_codes or item.code in retained_codes
                ]

        explicit_negative_pairs = {
            (operation.intent_type, operation.action_type)
            for operation in operations
            if operation.polarity is IntentPolarity.NEGATED
            and not any(
                code.startswith("explicit:retracted-")
                for code in operation.evidence_codes
            )
        }
        dropped_retraction_codes = {
            code
            for operation in operations
            if operation.polarity is IntentPolarity.NEGATED
            and (operation.intent_type, operation.action_type)
            in explicit_negative_pairs
            for code in operation.evidence_codes
            if code.startswith("explicit:retracted-")
        }
        if dropped_retraction_codes:
            operations = [
                operation
                for operation in operations
                if not any(
                    code in dropped_retraction_codes
                    for code in operation.evidence_codes
                )
            ]
            evidence = [
                item for item in evidence if item.code not in dropped_retraction_codes
            ]

        affirmed_operations = [
            operation
            for operation in operations
            if operation.polarity is IntentPolarity.AFFIRMED
        ]
        if (
            not affirmed_operations
            and context_available
            and context_cue is not None
            and (
                context_cue.actionful
                or context_cue.safe_override is not None
                or ACTION_MARKER_RE.search(
                    _affirmed_evidence_projection(matching_detection_text)
                )
                is None
            )
            and (
                previous_intent is not None
                or context_cue.safe_override is not None
                or context_cue.read_only_probe
            )
        ):
            previous = (
                previous_intent.primary_operation
                if previous_intent is not None
                else None
            )
            inherited_pair = (
                context_cue.safe_override
                if context_cue.safe_override is not None
                else (
                    _read_only_probe_pair(previous, context_topics, text)
                    if context_cue.read_only_probe
                    else (
                        (previous.intent_type, previous.action_type)
                        if previous is not None and not context_cue.actionful
                        else None
                    )
                )
            )
            if inherited_pair is not None:
                inherited_intent, inherited_action = inherited_pair
            else:
                inherited_intent = IntentType.UNKNOWN
                inherited_action = ActionType.UNKNOWN
            if (
                inherited_action in _CONTEXT_INHERITABLE_ACTIONS
                and (
                    previous is not None
                    or context_cue.safe_override is not None
                    or context_cue.read_only_probe
                )
            ):
                confidence = (
                    round(
                        min(max(previous.confidence * 0.72, 0.52), 0.69),
                        3,
                    )
                    if previous is not None
                    else 0.62
                )
                code = (
                    f"context:continue-{inherited_intent.value}."
                    f"{inherited_action.value}"
                )
                current_entities = self._extract_entities(text)
                operation_entities = {
                    key: list(current_entities.get(key, ()))
                    or (
                        list(previous.entities.get(key, ()))
                        if previous is not None
                        else []
                    )
                    or list(context_entities.get(key, ()))
                    for key in ("files", "functions", "classes", "languages")
                }
                inherited_keywords = self._stable_values(
                    [
                        *self._extract_keywords(text),
                        *(previous.keywords if previous is not None else ()),
                        *context_topics,
                        *sorted(context_reference_terms),
                    ]
                )
                cue_start, cue_end = context_cue.span
                inherited_clause = text[cue_start:cue_end].strip() or text
                operations.append(
                    IntentOperation(
                        intent_type=inherited_intent,
                        action_type=inherited_action,
                        polarity=IntentPolarity.AFFIRMED,
                        confidence=confidence,
                        clause=inherited_clause,
                        span=context_cue.span,
                        entities={
                            key: tuple(values)
                            for key, values in operation_entities.items()
                        },
                        keywords=tuple(inherited_keywords),
                        evidence_codes=(code,),
                    )
                )
                operations.sort(
                    key=lambda operation: (
                        operation.span[0] if operation.span is not None else 10**9,
                        operation.span[1] if operation.span is not None else 10**9,
                    )
                )
                evidence.append(
                    IntentEvidence(
                        code=code,
                        source=EvidenceSource.CONTEXT,
                        reason="current continuation refers to the prior safe operation",
                        strength=confidence,
                        span=context_cue.span,
                        turn_offset=-1,
                    )
                )
                context_used = True
        operations, evidence = self._reconcile_test_constraints(
            text,
            operations,
            evidence,
        )
        self._normalize_temporal_operation_order(text, operations)
        return tuple(operations), tuple(evidence), context_used

    def _reconcile_test_constraints(
        self,
        text: str,
        operations: list[IntentOperation],
        evidence: list[IntentEvidence],
    ) -> tuple[list[IntentOperation], list[IntentEvidence]]:
        """Project the shared typed test constraints onto parser operations.

        The ordinary pattern grammar remains responsible for affirmative
        commands.  This final authority pass has a narrower job:

        * current directives add exactly the denied test operation(s);
        * status/meta language removes test-shaped parser false positives;
        * a released prohibition removes only a negative interpretation and
          never manufactures positive execution authority.

        Keeping this projection at the end also prevents a broad clause-level
        negator from contaminating a later explicit command in the same turn.
        """

        constraints = extract_test_constraints(text)
        if not constraints:
            return operations, evidence

        action_map = {
            TestConstraintAction.CREATE: ActionType.CREATE,
            TestConstraintAction.EXECUTE: ActionType.EXECUTE,
        }

        def overlaps(
            operation: IntentOperation,
            constraint_span: tuple[int, int],
        ) -> bool:
            if operation.span is None:
                return False
            return (
                operation.span[0] < constraint_span[1]
                and constraint_span[0] < operation.span[1]
            )

        retained: list[IntentOperation] = []
        removed_codes: set[str] = set()
        for operation in operations:
            if operation.intent_type is not IntentType.TEST:
                retained.append(operation)
                continue
            relevant = tuple(
                constraint
                for constraint in constraints
                if action_map[constraint.action] is operation.action_type
                and overlaps(operation, constraint.span)
            )
            if not relevant:
                retained.append(operation)
                continue
            suppress = any(
                constraint.discourse
                in {ConstraintDiscourse.META, ConstraintDiscourse.STATUS}
                or (
                    constraint.state is TestConstraintState.RELEASED
                    and operation.polarity is IntentPolarity.NEGATED
                )
                or (
                    constraint.discourse is ConstraintDiscourse.DIRECTIVE
                    and constraint.state is TestConstraintState.DENIED
                )
                for constraint in relevant
            )
            if suppress:
                removed_codes.update(operation.evidence_codes)
            else:
                retained.append(operation)

        injected: list[IntentOperation] = []
        seen_constraints: set[
            tuple[ActionType, tuple[int, int], tuple[str, ...]]
        ] = set()
        for constraint in constraints:
            if (
                constraint.discourse is not ConstraintDiscourse.DIRECTIVE
                or constraint.state is not TestConstraintState.DENIED
            ):
                continue
            action_type = action_map[constraint.action]
            identity = (action_type, constraint.span, constraint.subtypes)
            if identity in seen_constraints:
                continue
            seen_constraints.add(identity)
            code = f"explicit:negated-test.{action_type.value}"
            injected.append(
                IntentOperation(
                    intent_type=IntentType.TEST,
                    action_type=action_type,
                    polarity=IntentPolarity.NEGATED,
                    confidence=0.99,
                    clause=constraint.text,
                    span=constraint.span,
                    entities={
                        "files": (),
                        "functions": (),
                        "classes": (),
                        "languages": (),
                    },
                    keywords=("test", *constraint.subtypes),
                    evidence_codes=(code, "rule:typed-test-constraint"),
                )
            )
            evidence.append(
                IntentEvidence(
                    code=code,
                    source=EvidenceSource.EXPLICIT,
                    reason="current directive denies this test operation",
                    strength=0.99,
                    span=constraint.span,
                )
            )

        retained_codes = {
            code for operation in retained for code in operation.evidence_codes
        }
        if removed_codes:
            evidence = [
                item
                for item in evidence
                if item.code not in removed_codes or item.code in retained_codes
            ]
        combined = [*retained, *injected]
        combined.sort(
            key=lambda operation: (
                operation.span[0] if operation.span is not None else 10**9,
                operation.span[1] if operation.span is not None else 10**9,
            )
        )
        return combined, evidence

    @staticmethod
    def _normalize_temporal_operation_order(
        text: str,
        operations: list[IntentOperation],
    ) -> None:
        """Apply one bounded semantic-order inversion for pre-update tests.

        Operation spans normally define execution order.  In ``升级依赖前先跑测``
        the update is mentioned first but the explicit temporal grammar puts
        the test first.  Reordering cannot create an operation or change its
        polarity; it only makes the existing ordered plan faithful.
        """

        for index in range(len(operations) - 1):
            update = operations[index]
            test = operations[index + 1]
            if (
                update.intent_type is not IntentType.CONFIGURE
                or update.action_type is not ActionType.UPDATE
                or update.polarity is not IntentPolarity.AFFIRMED
                or test.intent_type is not IntentType.TEST
                or test.action_type is not ActionType.EXECUTE
                or test.polarity is not IntentPolarity.AFFIRMED
                or update.span is None
                or test.span is None
            ):
                continue
            fragment = text[update.span[0] : test.span[1]]
            if _PRE_UPDATE_TEST_ORDER_RE.search(
                _mask_protected_intent_literals(fragment)
            ) is None:
                continue
            operations[index], operations[index + 1] = test, update
            return

    def _semantic_fallback_target(
        self,
        text: str,
        operations: tuple[IntentOperation, ...],
    ) -> tuple[str, tuple[int, int] | None] | None:
        """Return the one unresolved affirmative clause safe to classify.

        A completely unresolved request keeps the legacy whole-request
        fallback.  For a partially understood compound request, only one
        uncovered, non-negated clause is sent to the provider.  More than one
        unresolved clause stays deterministic: without provider-owned spans
        there is no safe way to restore their original order.
        """

        active_text = _mask_superseded_intent_prefix(text)
        active_text = mask_attributed_literal_statements(
            active_text, _protected_intent_literal_spans(active_text)
        )
        clauses = self._split_clauses(active_text)
        actionable_clauses = [
            (clause, span)
            for clause, span in clauses
            if not self._semantic_clause_is_non_instruction(
                clause, following_text=active_text[span[1] :]
            )
        ]
        if not actionable_clauses:
            return None
        if not operations and len(actionable_clauses) == len(clauses):
            if active_text == text:
                return text, None
            stripped = active_text.strip()
            if not stripped:
                return None
            start = len(active_text) - len(active_text.lstrip())
            return stripped, (start, start + len(stripped))
        unresolved: list[tuple[str, tuple[int, int]]] = []
        operation_spans = tuple(
            operation.span for operation in operations if operation.span is not None
        )
        for clause, clause_span in actionable_clauses:
            if is_clause_negated(clause):
                continue
            clause_start, clause_end = clause_span
            covered = any(
                operation_start < clause_end and clause_start < operation_end
                for operation_start, operation_end in operation_spans
            )
            if not covered:
                unresolved.append((clause, clause_span))
        return unresolved[0] if len(unresolved) == 1 else None

    def _semantic_clause_is_non_instruction(
        self, clause: str, *, following_text: str = ""
    ) -> bool:
        """Do not reoffer inert source material as missing command evidence.

        The deterministic filters and semantic admission must agree: otherwise
        removing a hypothetical deletion merely invites the model to recreate
        it. Unknown natural requests still qualify for normal fallback.
        """

        matching_clause = _mask_protected_intent_literals(clause)
        if re.search(r"\w", matching_clause) is None:
            return True
        if _POSTPOSED_SOURCE_ADVICE_RE.fullmatch(matching_clause):
            return True
        action_markers = tuple(ACTION_MARKER_RE.finditer(matching_clause))
        if action_markers and all(
            re.fullmatch(r"run(?:ning)?|execut(?:e|ing)|运行|执行|跑", marker[0], re.I)
            and _is_reported_execution_mention(
                matching_clause, marker.span(), following_text=following_text
            )
            for marker in action_markers
        ):
            return True
        report_prefix = _REPORTED_OPERATION_SOURCE_PREFIX_RE.match(matching_clause)
        if report_prefix is not None and re.search(
            r"\w", matching_clause[report_prefix.end() :]
        ) is None:
            return True
        candidates = [
            (candidate.action_type, candidate.trigger_span)
            for candidate in self._match_pattern_candidates(clause)
        ]
        vague = self._match_vague_action(clause)
        if vague is not None:
            candidates.append((vague[1], vague[2]))
        if not candidates:
            # A condition such as ``假设测试通过`` is context for the next
            # explicit request, not an unresolved test command of its own.
            return bool(
                _HYPOTHETICAL_RENAME_PREFIX_RE.search(
                    matching_clause.rstrip(" \t\r\n。.!！?？，,")
                )
            )
        if all(
            action is ActionType.EXECUTE
            and _is_reported_execution_mention(
                matching_clause, span, following_text=following_text
            )
            for action, span in candidates
        ):
            return True
        return bool(
            candidates
            and any(
                _HYPOTHETICAL_RENAME_PREFIX_RE.search(matching_clause[:span[0]])
                or _ACCIDENTAL_ACTION_PREFIX_RE.search(matching_clause[:span[0]])
                for _action, span in candidates
            )
            and all(
                action not in _READ_ONLY_CONTEXT_ACTIONS
                and _is_discussed_effectful_action(matching_clause, span)
                for action, span in candidates
            )
        )

    @staticmethod
    def _merge_semantic_evidence(
        deterministic: tuple[IntentEvidence, ...],
        semantic: tuple[IntentEvidence, ...],
    ) -> tuple[IntentEvidence, ...]:
        """Keep partial-fallback evidence in source order for diagnostics."""
        merged = [*deterministic, *semantic]
        merged.sort(
            key=lambda item: (
                item.span[0] if item.span is not None else 10**9,
                item.span[1] if item.span is not None else 10**9,
            )
        )
        return tuple(merged)

    @staticmethod
    def _merge_semantic_operations(
        deterministic: tuple[IntentOperation, ...],
        semantic: tuple[IntentOperation, ...],
    ) -> tuple[IntentOperation, ...]:
        if not semantic:
            return deterministic
        merged = [*deterministic, *semantic]
        # Python's stable sort preserves provider order for proposals tied to
        # one unresolved clause and deterministic order for legacy spans.
        merged.sort(
            key=lambda operation: (
                operation.span[0] if operation.span is not None else 10**9,
                operation.span[1] if operation.span is not None else 10**9,
            )
        )
        return tuple(merged)

    def _previous_intent(self, context: IntentContext) -> ParsedIntent | None:
        if context.previous_intent is not None:
            return context.previous_intent
        # ``active_task`` is a caller-maintained topic frame, not a user turn.
        # It may disambiguate entities and domains but cannot mint operation
        # authority for a bare ``continue``/``do that`` request.
        for turn in reversed(context.recent_turns):
            if turn.role != "user":
                continue
            candidate = self._parse_internal(
                turn.content,
                context=None,
                call_context=None,
                allow_semantic=False,
            )
            if candidate.primary_operation is not None:
                return candidate
        return None

    _CONTEXT_FRAME_IDENTITY_IGNORED_TERMS = frozenset(
        {
            "analyze",
            "analyse",
            "audit",
            "bug",
            "bugs",
            "change",
            "code",
            "debug",
            "failure",
            "failed",
            "fix",
            "inspect",
            "issue",
            "latency",
            "new",
            "old",
            "optimize",
            "performance",
            "plan",
            "profile",
            "py",
            "repair",
            "resolve",
            "review",
            "slow",
            "task",
            "timeout",
            "update",
        }
    )
    _CONTEXT_FRAME_IDENTITY_ALIASES = {
        "auth": "authentication",
        "db": "database",
        "docs": "documentation",
        "permissions": "permission",
        "router": "routing",
        "tests": "test",
    }

    def _context_frame_identity_terms(
        self,
        text: str,
        entities: Mapping[str, Sequence[str]],
    ) -> frozenset[str]:
        """Return subject terms that distinguish one canonical task frame."""

        targets = _context_entity_values(entities)
        terms: set[str] = set()
        values = [*self._extract_keywords(text)]
        for target in targets:
            values.extend(
                re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", target.casefold())
            )
        for value in values:
            normalized = str(value).strip().casefold()
            if not normalized or normalized in targets:
                continue
            for term in re.findall(
                r"[a-z0-9]+|[\u4e00-\u9fff]+",
                normalized,
            ):
                if (
                    len(term) > 1
                    and term not in self._CONTEXT_FRAME_IDENTITY_IGNORED_TERMS
                    and ACTION_MARKER_RE.fullmatch(term) is None
                ):
                    terms.add(
                        self._CONTEXT_FRAME_IDENTITY_ALIASES.get(term, term)
                    )
        return frozenset(terms)

    def _context_frame_conflicts(
        self,
        context: IntentContext,
        previous_intent: ParsedIntent | None,
    ) -> bool:
        """Return whether two caller-provided context frames disagree.

        ``active_task`` is the caller's canonical current frame.  A distinct
        concrete target or an explicitly ordinary-life active task must not be
        unioned with a stale ``previous_intent`` target.  The current user turn
        can still act on an explicit target; only deictic inheritance is
        blocked.
        """

        active_task = str(context.active_task or "").strip()
        if not active_task or previous_intent is None:
            return False
        if active_task.casefold() == previous_intent.raw_input.strip().casefold():
            return False
        if (
            _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(active_task)
            and _STRONG_SOFTWARE_SUBJECT_RE.search(active_task) is None
        ):
            return True
        active_targets = _context_entity_values(
            self._extract_entities(active_task)
        )
        previous_targets = _context_entity_values(previous_intent.entities)
        previous_operation = previous_intent.primary_operation
        if (
            not active_targets
            and previous_targets
            and previous_operation is not None
            and previous_operation.action_type in _UNSAFE_CONTEXT_ACTIONS
        ):
            # A differently worded canonical frame cannot silently inherit a
            # concrete object from a stale destructive/structural command,
            # even when their broad domain labels overlap (``auth`` is not
            # proof that ``auth.py`` is still the active target).
            return True
        if (
            active_targets
            and not previous_targets
            and previous_operation is not None
            and previous_operation.action_type in _UNSAFE_CONTEXT_ACTIONS
        ):
            # An unsafe prior with no concrete object (for example ``Run
            # pytest``) cannot override a canonical active frame that names a
            # new source target.
            return True
        if not active_targets and previous_targets:
            active_entities = self._extract_entities(active_task)
            active_identity = self._context_frame_identity_terms(
                active_task,
                active_entities,
            )
            previous_identity = self._context_frame_identity_terms(
                previous_intent.raw_input,
                previous_intent.entities,
            )
            if active_identity and active_identity.isdisjoint(previous_identity):
                # The caller-owned activeTask names a different subject even
                # though it has no file suffix.  Do not graft a concrete file
                # from a stale previous turn onto that canonical frame.
                return True
        return bool(
            active_targets
            and previous_targets
            and active_targets.isdisjoint(previous_targets)
        )

    def _context_topics(
        self,
        context: IntentContext,
        *,
        include_previous: bool = True,
    ) -> tuple[dict[str, list[str]], list[str]]:
        entities = {"files": [], "functions": [], "classes": [], "languages": []}
        topics: list[str] = []
        texts: list[str] = []
        if include_previous and context.previous_intent is not None:
            entities = self._merge_entities(
                entities,
                context.previous_intent.entities,
            )
        if context.active_task:
            # The active task is the caller's canonical frame. Older user and
            # assistant text may mention ruled-out hypotheses, so it must not
            # dilute an already informative active task.
            texts = [context.active_task]
        elif context.previous_intent is not None:
            texts = [context.previous_intent.raw_input]
        else:
            texts.extend(
                turn.content
                for turn in reversed(context.recent_turns)
                if turn.role == "user"
            )
            texts.extend(
                turn.content
                for turn in reversed(context.recent_turns)
                if turn.role != "user"
            )
        for value in texts:
            entities = self._merge_entities(entities, self._extract_entities(value))
            ordinary_non_software_subject = bool(
                _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(value)
                and _STRONG_SOFTWARE_SUBJECT_RE.search(value) is None
            )
            for keyword in self._extract_keywords(value):
                normalized = keyword.lower()
                if not _context_topic_is_engineering_grounded(value, normalized):
                    continue
                if ordinary_non_software_subject and normalized in {
                    "agent-orchestration",
                    "deployment-automation",
                    "git-workflow",
                }:
                    continue
                if normalized in _SAFE_CONTEXT_TOPICS and normalized not in topics:
                    topics.append(normalized)
        return entities, topics[:12]

    def _context_reference_terms(
        self,
        context: IntentContext,
    ) -> frozenset[str]:
        """Return bounded identifier-like terms safe for topic continuity.

        These terms only disambiguate an operation that omitted an entity;
        file paths and hyphenated Skill names are excluded so historical
        authority cannot become sticky context.
        """

        texts: list[str] = []
        if context.active_task:
            texts = [context.active_task]
        elif context.previous_intent is not None:
            texts = [context.previous_intent.raw_input]
        else:
            texts.extend(
                turn.content
                for turn in reversed(context.recent_turns)
                if turn.role == "user"
            )
        terms: set[str] = set()
        for value in texts:
            for keyword in self._extract_keywords(value):
                normalized = keyword.lower()
                if self._is_specific_reference_term(normalized):
                    terms.add(normalized)
        return frozenset(terms)

    @staticmethod
    def _specific_reference_terms(keywords: Sequence[str]) -> frozenset[str]:
        return frozenset(
            normalized
            for keyword in keywords
            if IntentParser._is_specific_reference_term(
                normalized := str(keyword).strip().lower()
            )
        )

    @staticmethod
    def _is_specific_reference_term(value: str) -> bool:
        return bool(
            value
            and value not in _SAFE_CONTEXT_TOPICS
            and re.fullmatch(r"[a-z][a-z0-9_]{2,}", value, re.IGNORECASE)
            and ACTION_MARKER_RE.fullmatch(value) is None
        )

    @staticmethod
    def _flatten_context_topics(
        entities: dict[str, list[str]], topics: list[str]
    ) -> list[str]:
        values: list[str] = []
        for key in ("files", "functions", "classes", "languages"):
            for value in entities.get(key, []):
                if value not in values:
                    values.append(value)
        for value in topics:
            if value not in values:
                values.append(value)
        return values[:16]

    @staticmethod
    def _stable_values(values: Sequence[str]) -> list[str]:
        unique: list[str] = []
        for value in values:
            normalized = str(value)
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    @classmethod
    def _coalesce_adjacent_operation(
        cls,
        left: IntentOperation,
        right: IntentOperation,
        text: str,
    ) -> IntentOperation | None:
        """Merge duplicate parser hits that describe one logical action."""
        if (
            left.intent_type is not right.intent_type
            or left.action_type is not right.action_type
            or left.polarity is not right.polarity
            or left.span is None
            or right.span is None
        ):
            return None
        gap = text[left.span[1] : right.span[0]]
        left_targets = _context_entity_values(left.entities)
        right_targets = _context_entity_values(right.entities)
        anaphoric_update_continuation = bool(
            left.action_type is ActionType.UPDATE
            and left_targets
            and not right_targets
            and re.fullmatch(
                r"\s*(?:make|apply)\s+(?:the|this|that)\s+change\s*[。.!！]?\s*|"
                r"\s*update\s+(?:it|this|that)\s*[。.!！]?\s*|"
                r"\s*(?:直接|现在|马上|立即)?\s*(?:更新|修改|改)(?:它)?"
                r"\s*[。.!！]?\s*",
                right.clause,
                re.IGNORECASE,
            )
        )
        if not anaphoric_update_continuation and re.search(
            r"[.!?;。！？；]|\b(?:then|meanwhile)\b|"
            r"(?:再|随后|接着|同时|完成后|确认后)",
            gap,
            re.IGNORECASE,
        ):
            return None
        if left.span[1] != right.span[0] and not anaphoric_update_continuation:
            connector_carries_reference = bool(
                re.fullmatch(r"\s*(?:and\b|&|并|且)\s*", gap, re.IGNORECASE)
                and _PRIOR_RESULT_REFERENCE_RE.search(right.clause)
            )
            if not connector_carries_reference and not re.match(
                r"^(?:and\b|并|且)",
                right.clause,
                re.IGNORECASE,
            ):
                return None
        entities = cls._merge_entities(
            {key: list(values) for key, values in left.entities.items()},
            right.entities,
        )
        merged_span = (left.span[0], right.span[1])
        return IntentOperation(
            intent_type=left.intent_type,
            action_type=left.action_type,
            polarity=left.polarity,
            confidence=max(left.confidence, right.confidence),
            clause=text[merged_span[0] : merged_span[1]].strip(),
            span=merged_span,
            entities={key: tuple(values) for key, values in entities.items()},
            keywords=tuple(
                cls._stable_values([*left.keywords, *right.keywords])
            ),
            evidence_codes=tuple(
                cls._stable_values(
                    [*left.evidence_codes, *right.evidence_codes]
                )
            ),
        )

    @staticmethod
    def _merge_entities(
        left: dict[str, list[str]], right: Mapping[str, Sequence[str]]
    ) -> dict[str, list[str]]:
        merged = {
            "files": list(left.get("files", [])),
            "functions": list(left.get("functions", [])),
            "classes": list(left.get("classes", [])),
            "languages": list(left.get("languages", [])),
        }
        for key, merged_values in merged.items():
            for value in right.get(key, ()):
                normalized = str(value)
                if normalized and normalized not in merged_values:
                    merged_values.append(normalized)
        return merged

    @staticmethod
    def _split_clauses(text: str) -> list[tuple[str, tuple[int, int]]]:
        coarse_clauses: list[tuple[str, tuple[int, int]]] = []
        start = 0
        matching_text = _mask_protected_intent_literals(text)
        request_resets = question_request_resets(matching_text)
        if request_resets:
            pieces: list[tuple[str, tuple[int, int]]] = []
            segment_start = 0
            for reset_start, reset_end in (*request_resets, (len(text), len(text))):
                for clause, (left, right) in IntentParser._split_clauses(
                    text[segment_start:reset_start]
                ):
                    pieces.append((clause, (segment_start + left, segment_start + right)))
                segment_start = reset_end
            return pieces
        visible_start, visible_end = _non_whitespace_bounds(matching_text)
        if visible_start == visible_end:
            return []
        for match in _CLAUSE_SEPARATOR_RE.finditer(
            matching_text,
            visible_start,
            visible_end,
        ):
            separator = matching_text[match.start() : match.end()].strip()
            if (
                separator in {"。", "！", "？"}
                and re.match(
                    r"先\s*(?:请|帮我|麻烦)?\s*分析",
                    matching_text[match.end() :],
                )
                and _HYPOTHETICAL_RENAME_PREFIX_RE.search(
                    matching_text[start : match.start()]
                ) is None
            ):
                # A factual report supplies the subject of its analysis.
                # Splitting it creates two unresolved requests and prevents
                # semantic fallback from classifying either one. Only close
                # a hypothetical frame here so its discussed action cannot
                # govern the following affirmative analysis.
                continue
            if (
                separator in {",", "，"}
                and re.match(
                    r"(?:请(?:你)?|帮我|麻烦(?:你)?|please\b|kindly\b)",
                    matching_text[match.end() :],
                    re.IGNORECASE,
                )
                and _EVERYDAY_NON_SOFTWARE_SUBJECT_RE.search(
                    matching_text[start : match.start()]
                )
                and _STRONG_SOFTWARE_SUBJECT_RE.search(
                    matching_text[start : match.start()]
                ) is None
            ):
                # Keep an explicit everyday subject attached to its polite
                # follow-up. Otherwise ``my car is slow, please check why``
                # loses the subject and becomes an apparent software defect.
                continue
            if (
                separator.casefold() == "and"
                and any(
                    denial.start() < match.start() < denial.end()
                    for denial in _COORDINATED_ENGLISH_TEST_DENIAL_RE.finditer(
                        matching_text
                    )
                )
            ):
                # ``skip adding tests and skip running them`` is one atomic
                # compound prohibition.  Splitting at ``and`` loses the test
                # object needed to resolve the anaphoric second half.
                continue
            if (
                separator.casefold() == "and"
                and any(
                    request.start() < match.start() < request.end()
                    for request in _COORDINATED_ENGLISH_TEST_REQUEST_RE.finditer(
                        matching_text
                    )
                )
            ):
                # The shared object in ``create and run regression tests``
                # belongs to both positive operations.
                continue
            if (
                separator == "也"
                and re.search(
                    r"(?:测试|单测|测例|用例)[^,，。！？;；\n]{0,16}"
                    r"(?:既\s*)?不\s*(?:新增|添加|创建|编写|写|改)\s*$",
                    matching_text[start : match.start()],
                )
                and re.match(
                    r"\s*不\s*(?:执行|运行|跑)",
                    matching_text[match.end() :],
                )
            ):
                # Coordinated prohibitions grant neither half: keep
                # ``测试既不新增也不执行`` in one clause so CREATE and EXECUTE
                # both survive as explicit negative constraints.
                continue
            if (
                separator == "再"
                and ACTION_MARKER_RE.search(
                    matching_text[start : match.start()]
                )
                is None
                and _TECHNICAL_MARKER_RE.search(
                    matching_text[start : match.start()]
                )
                and re.match(
                    r"\s*(?:审(?:一遍|一?下)?|优化|调优)",
                    matching_text[match.end() :],
                )
            ):
                # In ``登录权限问题再审一遍`` and ``慢查询再优化`` the
                # postfix verb is the only requested action, not a new clause
                # following an actionless fragment.
                continue
            if any(
                view_match.start() <= match.start()
                and match.end() <= view_match.end()
                for view_match in _CHINESE_VIEW_SWITCH_FROM_CHANGE_RE.finditer(
                    matching_text
                )
            ):
                # ``改为再看`` is one read-only target switch.  The normal
                # sequencing meaning of ``再`` must not split its grammar.
                continue
            if (
                separator == "再"
                and re.search(
                    r"(?:pytest|pyetst|tests?|test\s+suite|测试|单测|用例)\s*$",
                    matching_text[start : match.start()],
                    re.IGNORECASE,
                )
                and re.match(
                    r"\s*(?:跑(?:一遍|一下)?|运行|执行|run|execute)",
                    matching_text[match.end() :],
                    re.IGNORECASE,
                )
            ):
                # Subject-first rerun requests commonly use ``再`` between
                # the runner and verb: ``pytest 再跑一遍``.
                continue
            if (
                separator in {"也", "都"}
                and _POSTPOSED_TEST_DENIAL_PREFIX_RE.search(
                    matching_text[start : match.start()].strip()
                )
                and _POSTPOSED_TEST_DENIAL_SUFFIX_RE.match(
                    matching_text[match.end() :]
                )
            ):
                # ``一条测试也别添`` and ``测试一行都不许写`` are one
                # postposed prohibition. Splitting at the coordinator would
                # hide both the test object and its denied creation verb.
                continue
            if (
                separator == "随后"
                and re.match(
                    r"\s*[,，]",
                    matching_text[match.end() :],
                )
            ):
                # In the compact order declaration ``实现随后，...`` the
                # connector is postfix metadata for the implementation; it
                # does not introduce an empty next clause.
                continue
            if (
                separator == "最后"
                and re.search(
                    r"(?:readme|docs?|documentation|文档|说明)\s*$",
                    matching_text[start : match.start()],
                    re.IGNORECASE,
                )
                and re.match(
                    r"\s*(?:补|更新|修订|改)",
                    matching_text[match.end() :],
                )
            ):
                # ``README 最后补`` is subject + order adverb + action.  Keep
                # the subject attached to its verb for document routing.
                continue
            if (
                separator in {"再", "然后", "接着", "随后"}
                and _LEADING_NEGATED_SEQUENCE_RE.fullmatch(
                    matching_text[start : match.start()]
                )
                is not None
            ):
                # ``不要先跑测试再改代码`` rejects the complete
                # choreographed sequence. Splitting at ``再`` would silently
                # manufacture write authority for its second operation.
                continue
            if (
                _STRUCTURAL_MOVE_SUBJECT_RE.search(
                    matching_text[start : match.start()]
                )
                and _STRUCTURAL_REFERENCE_UPDATE_RE.match(
                    matching_text[match.end() :]
                )
            ):
                # Keep a structural move and its reference update in one
                # clause so the first operation is classified as rename-like
                # rather than a generic filesystem move.
                continue
            raw = text[start : match.start()]
            matching_raw = matching_text[start : match.start()]
            stripped = raw.strip()
            if matching_raw.strip() and stripped:
                left = start + len(raw) - len(raw.lstrip())
                coarse_clauses.append((stripped, (left, left + len(stripped))))
            start = match.end()
        raw = text[start:]
        matching_raw = matching_text[start:]
        stripped = raw.strip()
        if matching_raw.strip() and stripped:
            left = start + len(raw) - len(raw.lstrip())
            coarse_clauses.append((stripped, (left, left + len(stripped))))
        if not coarse_clauses and matching_text.strip():
            coarse_clauses = [(text, (0, len(text)))]

        clauses: list[tuple[str, tuple[int, int]]] = []
        for clause, clause_span in coarse_clauses:
            matching_clause = _mask_protected_intent_literals(clause)
            boundaries: list[int] = [
                match.end()
                for match in _NOMINAL_TEST_DENIAL_FOLLOWUP_RE.finditer(
                    matching_clause
                )
            ]
            for match in _UNPUNCTUATED_ACTION_BOUNDARY_RE.finditer(
                matching_clause
            ):
                if match.start() <= 0:
                    continue
                if any(
                    denial.start() < match.start() < denial.end()
                    for denial in _COORDINATED_TEST_DENIAL_RE.finditer(
                        matching_clause
                    )
                ):
                    # Internal polarity markers in ``既不新增也不执行`` do
                    # not start independent clauses. Both prohibited actions
                    # must reach downstream Skill constraint checks.
                    continue
                if any(
                    view_match.start() < match.start() < view_match.end()
                    for view_match in _CHINESE_VIEW_SWITCH_FROM_CHANGE_RE.finditer(
                        matching_clause
                    )
                ):
                    # ``只/仅`` is a modifier inside ``改为只查看`` rather
                    # than the start of an independent clause.
                    continue
                prefix = matching_clause[: match.start()].strip()
                if (
                    re.search(
                        r"[\w/\\._-]+\.(?:py|js|ts|jsx|tsx|java|go|rs|cpp|"
                        r"c|h|json|yaml|yml|toml)[^,，。！？!?;；\n]{0,40}"
                        r"(?:逻辑|实现|代码)\s*$",
                        prefix,
                        re.IGNORECASE,
                    )
                    and re.match(
                        r"直接\s*(?:改好|修好|更新|修改)",
                        matching_clause[match.start() :],
                    )
                ):
                    # ``把 foo.py 里的重命名逻辑直接改好`` is one update.
                    # The generic no-punctuation splitter otherwise mistakes
                    # ``直接改`` for a new targetless command.
                    continue
                if (
                    re.fullmatch(r"(?:测试|单测|测例|用例)", prefix)
                    and re.match(
                        r"(?:代码|文件|用例)\s*"
                        r"(?:不要|别|不许|不得|不准|不允许)\s*"
                        r"(?:动|改|修改|变更|碰)",
                        matching_clause[match.start() :],
                    )
                ):
                    # ``测试代码别动`` is one artifact-preservation clause;
                    # ``代码`` must not become a production-code boundary.
                    continue
                scoped_test_subtype = _TEST_SCOPE_SUBTYPE_SUFFIX_RE.search(prefix)
                if (
                    scoped_test_subtype is not None
                    and _POSTFIX_TEST_EXECUTION_DENIAL_RE.match(
                        matching_clause[match.start() :]
                    )
                ):
                    # The no-punctuation boundary detector sees the generic
                    # ``测试别跑`` suffix. Move the boundary left to retain its
                    # immediately adjacent subtype (``慢速``/``浏览器``), so
                    # the subtype cannot leak into a preceding allowed test.
                    boundaries.append(scoped_test_subtype.start())
                    continue
                if (
                    _TEST_ARTIFACT_DENIAL_PREFIX_RE.search(prefix)
                    and _TEST_ARTIFACT_MODIFICATION_DENIAL_SUFFIX_RE.match(
                        matching_clause[match.start() :]
                    )
                ):
                    # Keep the specific test artifact attached to a denied
                    # modification.  A broader ``测试...代码不准改`` sequence
                    # still splits so execution and production-code denials
                    # remain independent operations.
                    continue
                if (
                    _POSTPOSED_TEST_DENIAL_PREFIX_RE.search(prefix)
                    and _POSTPOSED_TEST_DENIAL_SUFFIX_RE.match(
                        matching_clause[match.start() :]
                    )
                ):
                    # Keep nominal prohibitions such as ``测试一行也不许写``
                    # intact so the TEST rule, rather than generic document
                    # creation, owns the denied operation.
                    continue
                negated_explanation_prefix = (
                    _NEGATED_EXPLANATION_PREFIX_RE.fullmatch(prefix)
                    is not None
                )
                if (
                    ACTION_MARKER_RE.search(prefix) is None
                    and not negated_explanation_prefix
                    and _UNPUNCTUATED_READONLY_PREFIX_RE.fullmatch(prefix)
                    is None
                ):
                    continue
                if (
                    _DOUBLE_NEGATION_BOUNDARY_PREFIX_RE.search(prefix)
                    and re.match(
                        r"不\s*(?:运行|执行|跑|修复|修|修改|更新|改|"
                        r"补|写|删除|删)",
                        matching_clause[match.start() :],
                    )
                ):
                    # ``不用不跑`` is a positive double-negative idiom.  The
                    # inner ``不跑`` cannot start a new prohibition clause.
                    continue
                if (
                    re.match(
                        r"直接(?:\s*(?:fix|repair|modify|update|change|edit|"
                        r"write|run|execute)\b|把|将|修复|修|修改|更新|改|补|"
                        r"写|运行|执行|跑)",
                        matching_clause[match.start() :],
                    )
                    and not negated_explanation_prefix
                    and _DIRECT_ACTION_EXPLANATION_PREFIX_RE.fullmatch(prefix)
                    is not None
                ):
                    # A ``直接…`` phrase may be the object of an explanation
                    # ("解释直接修复为什么危险").  It is a task boundary
                    # only after a complete, explicitly negated explanation.
                    continue
                if (
                    _INSTRUCTIONAL_SUBJECT_FRAGMENT_RE.fullmatch(prefix)
                    and re.match(
                        r"(?:给我|帮我|请)\s*(?:解释|说明)",
                        matching_clause[match.start() :],
                    )
                ):
                    # ``测试命令`` is the object of the following explanation,
                    # not an execute operation followed by a new task.
                    continue
                boundaries.append(match.start())
            local_starts = [0, *sorted(set(boundaries)), len(clause)]
            for left, right in zip(local_starts, local_starts[1:]):
                raw_part = clause[left:right]
                matching_part = matching_clause[left:right]
                part = raw_part.strip()
                if not matching_part.strip() or not part:
                    continue
                local_left = left + len(raw_part) - len(raw_part.lstrip())
                absolute_left = clause_span[0] + local_left
                clauses.append(
                    (part, (absolute_left, absolute_left + len(part)))
                )
        if matching_text.strip():
            return clauses
        return []

    @staticmethod
    def _match_vague_action(
        clause: str,
    ) -> tuple[IntentType, ActionType, tuple[int, int]] | None:
        matching_clause = _mask_explicit_skill_identifiers(
            _mask_quoted_intent_examples(clause)
        )
        visible_start, visible_end = _non_whitespace_bounds(matching_clause)
        if visible_start == visible_end:
            return None
        matching_window = matching_clause[visible_start:visible_end]
        matches: list[
            tuple[int, int, IntentType, ActionType, tuple[int, int]]
        ] = []
        for pattern_order, (pattern, intent_type, action_type) in enumerate(
            _VAGUE_ACTION_RULES
        ):
            match = pattern.search(matching_window)
            if match is not None:
                matches.append(
                    (
                        match.start(),
                        pattern_order,
                        intent_type,
                        action_type,
                        (
                            visible_start + match.start(),
                            visible_start + match.end(),
                        ),
                    )
                )
        if not matches:
            return None
        _start, _order, intent_type, action_type, span = min(matches)
        return intent_type, action_type, span

    @staticmethod
    def _evidence_reason(source: EvidenceSource, polarity: IntentPolarity) -> str:
        if polarity is IntentPolarity.NEGATED:
            return "current clause explicitly rules out this operation"
        if source is EvidenceSource.CONTEXT:
            return "current action is resolved against a recent topic"
        if source is EvidenceSource.SEMANTIC:
            return "semantic fallback supports this operation"
        return "current clause contains a direct action and technical target"

    @staticmethod
    def _direct_confidence(
        match_confidence: float, entities: dict[str, list[str]]
    ) -> float:
        confidence = 0.86 + min(max(match_confidence, 0.0), 1.0) * 0.06
        if any(entities.values()):
            confidence += 0.04
        return round(min(confidence, 0.98), 3)

    @staticmethod
    def _select_primary_operation(
        operations: tuple[IntentOperation, ...],
    ) -> int | None:
        affirmed = [
            (index, operation)
            for index, operation in enumerate(operations)
            if operation.polarity is IntentPolarity.AFFIRMED
        ]
        if not affirmed:
            return None
        return max(
            affirmed,
            key=lambda item: (
                _ACTION_PRIORITY.get(item[1].action_type, 0),
                -item[0],
                item[1].confidence,
            ),
        )[0]

    def _validate_semantic_proposals(
        self,
        proposals: Sequence[SemanticIntentProposal],
        text: str,
        *,
        span: tuple[int, int] | None = None,
    ) -> tuple[tuple[IntentOperation, ...], tuple[IntentEvidence, ...]]:
        operations: list[IntentOperation] = []
        evidence: list[IntentEvidence] = []
        affirmed_technical, excluded_technical = _technical_evidence_polarity(
            text
        )
        excluded_only_technical_context = bool(
            excluded_technical and not affirmed_technical
        )
        masked_request = _mask_protected_intent_literals(text)
        explanation_only = (
            is_bounded_explanation_request(masked_request)
            or is_bounded_readonly_request(masked_request)
        )
        for proposal in tuple(proposals or ())[:4]:
            if not isinstance(proposal, SemanticIntentProposal):
                continue
            try:
                intent_type = IntentType(proposal.intent_type)
                action_type = ActionType(proposal.action_type)
                polarity = IntentPolarity(proposal.polarity)
                confidence = float(proposal.confidence)
            except (TypeError, ValueError):
                continue
            if (
                intent_type is IntentType.UNKNOWN
                or action_type is ActionType.UNKNOWN
                or action_type in _UNSAFE_CONTEXT_ACTIONS
                or action_type
                not in SEMANTIC_INTENT_ALLOWED_ACTIONS.get(intent_type, frozenset())
                or polarity is not IntentPolarity.AFFIRMED
                or not 0.5 <= confidence <= 0.85
            ):
                continue
            if explanation_only and action_type not in _READ_ONLY_CONTEXT_ACTIONS:
                # A model may resolve the explanation label, but the local
                # request frame cannot authorize mutation or execution.
                continue
            if (
                excluded_only_technical_context
                and intent_type
                in {
                    IntentType.CODE,
                    IntentType.CONFIGURE,
                    IntentType.DEBUG,
                    IntentType.REFACTOR,
                    IntentType.TEST,
                }
            ):
                # A provider may resolve colloquial language, but it cannot use
                # an explicitly excluded software noun as the only evidence
                # for engineering authority (``update my itinerary, not code``).
                continue
            # A semantic provider classifies the current user request; it does
            # not get to author retained clauses, spans, or evidence labels.
            # Keeping those fields local prevents model output from becoming
            # routing authority or durable diagnostic text.
            code = f"semantic:proposal-{intent_type.value}.{action_type.value}"
            operation = IntentOperation(
                intent_type=intent_type,
                action_type=action_type,
                polarity=polarity,
                confidence=round(confidence, 3),
                clause=text[:500],
                span=span,
                keywords=tuple(
                    self._extract_keywords(
                        _affirmed_evidence_projection(text)
                    )
                ),
                evidence_codes=(code,),
            )
            operations.append(operation)
            evidence.append(
                IntentEvidence(
                    code=code,
                    source=EvidenceSource.SEMANTIC,
                    reason="semantic fallback supports this operation",
                    strength=operation.confidence,
                    span=span,
                )
            )
        return tuple(operations), tuple(evidence)

    def _match_pattern_candidates(self, text: str) -> tuple[_PatternCandidate, ...]:
        source_aligned_matching_text = _mask_explicit_skill_identifiers(
            _mask_quoted_intent_examples(text)
        )
        visible_start, visible_end = _non_whitespace_bounds(
            source_aligned_matching_text
        )
        if visible_start == visible_end:
            return ()
        matching_text = source_aligned_matching_text[visible_start:visible_end]

        def source_aligned(
            candidates: Sequence[_PatternCandidate],
        ) -> tuple[_PatternCandidate, ...]:
            if visible_start == 0:
                return tuple(candidates)
            return tuple(
                replace(
                    candidate,
                    match_span=(
                        visible_start + candidate.match_span[0],
                        visible_start + candidate.match_span[1],
                    ),
                    trigger_span=(
                        visible_start + candidate.trigger_span[0],
                        visible_start + candidate.trigger_span[1],
                    ),
                )
                for candidate in candidates
            )

        capability_question = _CAPABILITY_QUESTION_RE.search(matching_text)
        if capability_question is not None:
            return source_aligned(
                (
                    _PatternCandidate(
                        intent_type=IntentType.QUESTION,
                        action_type=ActionType.READ,
                        confidence=1.0,
                        match_span=capability_question.span(),
                        trigger_span=capability_question.span(),
                        pattern_order=-1,
                    ),
                )
            )
        system_catalog_query = _SYSTEM_CATALOG_QUERY_RE.search(matching_text)
        if (
            system_catalog_query is None
            and _ORDINARY_PHYSICAL_TOOL_DOMAIN_RE.search(matching_text) is None
        ):
            system_catalog_query = _NOISY_SYSTEM_CATALOG_QUERY_RE.search(
                matching_text
            )
        if system_catalog_query is None:
            system_catalog_query = _TERSE_SYSTEM_CATALOG_QUERY_RE.search(
                matching_text
            )
        if (
            system_catalog_query is None
            and _ORDINARY_PHYSICAL_TOOL_DOMAIN_RE.search(matching_text)
            is None
        ):
            system_catalog_query = _SYSTEM_CATALOG_QUERY_FRAGMENT_RE.search(
                matching_text
            )

        by_trigger: dict[tuple[int, int], _PatternCandidate] = {}
        for pattern_order, (pattern, intent, action) in enumerate(self._pattern_cache):
            for match in pattern.finditer(matching_text):
                explicit_action_spans = [
                    match.span(group_name)
                    for group_name in match.re.groupindex
                    if (
                        group_name == "action"
                        or group_name.startswith("action_")
                    )
                    and match.start(group_name) >= 0
                ]
                explicit_action_span = (
                    min(explicit_action_spans)
                    if explicit_action_spans
                    else None
                )
                trigger_span = explicit_action_span or (
                    first_action_span(matching_text, match.span())
                    or match.span()
                )
                score = 1.0 - (
                    (visible_start + match.start()) / max(len(text), 1)
                ) * 0.3
                candidate = _PatternCandidate(
                    intent_type=intent,
                    action_type=action,
                    confidence=score,
                    match_span=match.span(),
                    trigger_span=trigger_span,
                    pattern_order=pattern_order,
                )
                current = by_trigger.get(trigger_span)
                document_target = (
                    _DOCUMENT_ACTION_TARGET_RE.search(
                        matching_text[trigger_span[1] :]
                    )
                    is not None
                )
                if current is None or (
                    bool(
                        document_target and candidate.intent_type is IntentType.DOCUMENT
                    ),
                    candidate.confidence,
                    -candidate.pattern_order,
                ) > (
                    bool(
                        document_target and current.intent_type is IntentType.DOCUMENT
                    ),
                    current.confidence,
                    -current.pattern_order,
                ):
                    by_trigger[trigger_span] = candidate
        ordered = sorted(
            by_trigger.values(),
            key=lambda candidate: (
                candidate.trigger_span[0],
                candidate.pattern_order,
            ),
        )

        ordered = [
            candidate
            for candidate in ordered
            if not (
                candidate.intent_type is IntentType.DOCUMENT
                and candidate.action_type is ActionType.CREATE
                and matching_text[
                    max(0, candidate.trigger_span[0] - 2) :
                    candidate.trigger_span[0]
                ]
                == "测先"
            )
        ]

        # Noun-shaped fallback rules may sit wholly inside a stronger phrase
        # rule (``Debug this pytest failure`` used to yield the same operation
        # twice).  Prefer the widest interval for an identical operation while
        # leaving distinct operations and sequential, non-overlapping repeats
        # untouched.
        ordered = [
            candidate
            for candidate in ordered
            if not any(
                other is not candidate
                and other.intent_type is candidate.intent_type
                and other.action_type is candidate.action_type
                and other.match_span != candidate.match_span
                and other.match_span[0] <= candidate.match_span[0]
                and candidate.match_span[1] <= other.match_span[1]
                for other in ordered
            )
        ]

        without_overlapping_echoes: list[_PatternCandidate] = []
        for candidate in ordered:
            if any(
                previous.intent_type is candidate.intent_type
                and previous.action_type is candidate.action_type
                and candidate.match_span[0] < previous.match_span[1]
                and previous.match_span[0] < candidate.match_span[1]
                for previous in without_overlapping_echoes
            ):
                continue
            without_overlapping_echoes.append(candidate)
        ordered = without_overlapping_echoes

        # A second generic verb in an infinitive phrase describes the purpose
        # of the first operation (``refactor router.py to simplify it``), not
        # another independently ordered refactor. This can span two regex
        # rules, so pattern-order deduplication alone is insufficient.
        without_infinitive_echoes: list[_PatternCandidate] = []
        for candidate in ordered:
            echoed = any(
                previous.intent_type is candidate.intent_type
                and previous.action_type is candidate.action_type
                and re.fullmatch(
                    r"\s+to\s+",
                    matching_text[
                        previous.match_span[1] : candidate.trigger_span[0]
                    ],
                    re.IGNORECASE,
                )
                is not None
                for previous in without_infinitive_echoes
            )
            if not echoed:
                without_infinitive_echoes.append(candidate)
        ordered = without_infinitive_echoes

        # A noun-only taxonomy match inside an explicit action match enriches
        # that action's topic; it is not a second requested operation. For
        # example, ``audit persistent memory`` is one audit operation, not an
        # audit followed by a request to create memory.
        explicit_matches = tuple(
            candidate
            for candidate in ordered
            if candidate.trigger_span != candidate.match_span
        )
        ordered = [
            candidate
            for candidate in ordered
            if candidate.trigger_span != candidate.match_span
            or not any(
                other is not candidate
                and other.match_span[0] <= candidate.match_span[0]
                and candidate.match_span[1] <= other.match_span[1]
                for other in explicit_matches
            )
        ]

        # One regex may match both an outer action and its infinitive purpose
        # (``refactor ... to simplify ...``). Keep the first trigger from that
        # rule; real sequential repeats are split at clause connectors first.
        deduplicated: list[_PatternCandidate] = []
        seen_rule_operations: set[tuple[int, IntentType, ActionType]] = set()
        for candidate in ordered:
            identity = (
                candidate.pattern_order,
                candidate.intent_type,
                candidate.action_type,
            )
            if identity in seen_rule_operations:
                continue
            seen_rule_operations.add(identity)
            deduplicated.append(candidate)
        incidental_catalog_review = bool(deduplicated) and all(
            candidate.intent_type is IntentType.REVIEW
            and re.match(
                r"\s*(?:看看|看一看)",
                matching_text[candidate.match_span[0] : candidate.match_span[1]],
            )
            is not None
            for candidate in deduplicated
        )
        if (
            system_catalog_query is not None
            and _SYSTEM_CATALOG_REQUESTED_WORK_RE.search(matching_text) is None
        ):
            query_span = next(
                system_catalog_query.span(group_name)
                for group_name in (
                    "query",
                    "query_zh",
                    "query_en",
                    "query_skill",
                    "query_terse",
                    "query_fragment",
                )
                if group_name in system_catalog_query.re.groupindex
                and system_catalog_query.start(group_name) >= 0
            )
            catalog_candidate = _PatternCandidate(
                intent_type=IntentType.QUESTION,
                action_type=ActionType.READ,
                confidence=1.0,
                match_span=system_catalog_query.span(),
                trigger_span=query_span,
                pattern_order=-1,
            )
            if not deduplicated or incidental_catalog_review:
                return source_aligned((catalog_candidate,))
            if all(
                is_span_negated(matching_text, candidate.trigger_span)
                for candidate in deduplicated
            ):
                return source_aligned(
                    sorted(
                        [catalog_candidate, *deduplicated],
                        key=lambda candidate: (
                            candidate.trigger_span[0],
                            candidate.pattern_order,
                        ),
                    )
                )
        return source_aligned(deduplicated)

    @staticmethod
    def _operation_segment(
        clause: str,
        clause_span: tuple[int, int],
        candidates: tuple[_PatternCandidate, ...],
        index: int,
    ) -> tuple[str, tuple[int, int]]:
        candidate = candidates[index]
        # The first action owns its clause's leading subject/topic. Starting
        # at the trigger discarded inputs such as "the fixture failed; locate
        # the cause" and left routing with only the generic action words.
        local_start = 0 if index == 0 else candidate.match_span[0]
        local_end = len(clause)
        if index + 1 < len(candidates):
            next_start = candidates[index + 1].match_span[0]
            if next_start > local_start:
                local_end = next_start
        raw = clause[local_start:local_end]
        operation_text = raw.strip()
        left = local_start + len(raw) - len(raw.lstrip())
        right = left + len(operation_text)
        return operation_text, (clause_span[0] + left, clause_span[0] + right)

    def _extract_entities(self, text: str) -> dict[str, list[str]]:
        entities: dict[str, list[str]] = {
            "files": [],
            "functions": [],
            "classes": [],
            "languages": [],
        }
        matching_text = _mask_protected_intent_literals(text)

        entities["files"] = list(extract_file_tokens(matching_text))

        func_pattern = re.compile(
            r"\b(def|fn|func|function)\s+([\w_]+)\b", re.IGNORECASE
        )
        for m in func_pattern.finditer(matching_text):
            if m.group(2) not in entities["functions"]:
                entities["functions"].append(m.group(2))

        class_pattern = re.compile(r"\bclass\s+([\w_]+)\b", re.IGNORECASE)
        for m in class_pattern.finditer(matching_text):
            if m.group(1) not in entities["classes"]:
                entities["classes"].append(m.group(1))

        lang_pattern = re.compile(
            r"\b(python|javascript|typescript|java|go|rust|cpp|c\+\+|react|vue)\b",
            re.IGNORECASE,
        )
        for m in lang_pattern.finditer(matching_text):
            lang = m.group(1).lower()
            if lang not in entities["languages"]:
                entities["languages"].append(lang)

        return entities

    def _extract_keywords(self, text: str) -> list[str]:
        stopwords = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "can",
            "need",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "under",
            "again",
            "further",
            "then",
            "once",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "any",
            "both",
            "each",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "nor",
            "not",
            "only",
            "own",
            "same",
            "so",
            "than",
            "too",
            "very",
            "just",
            "and",
            "but",
            "if",
            "or",
            "because",
            "until",
            "while",
            "this",
            "that",
            "these",
            "those",
            "i",
            "me",
            "my",
            "we",
            "our",
            "you",
            "your",
            "he",
            "him",
            "his",
            "she",
            "her",
            "it",
            "its",
            "they",
            "them",
            "their",
            "what",
            "which",
            "who",
            "whom",
            # These are the exact trigger verbs the EXPLAIN/CONFIGURE
            # patterns above require additional context for (see
            # _EXPLAIN_PATTERNS/_CONFIGURE_PATTERNS). Left unfiltered,
            # they still leaked out of keyword extraction as an
            # independent, context-free signal and could route
            # unrelated small talk through a coincidental keyword
            # match on some skill's own description/example text.
            "tell",
            "describe",
            "explain",
            "configure",
            "setup",
            "install",
            "init",
            "initialize",
            # A bare PIN is at least as likely to be a credential or a UI
            # marker as a dependency version.  Dependency phrases receive
            # explicit ``dependency``/``version`` aliases instead.
            "pin",
        }
        normalized_text = _mask_explicit_skill_identifiers(
            _mask_protected_intent_literals(text)
        ).lower()
        words = re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]+", normalized_text)
        keywords = [w for w in words if w not in stopwords and len(w) > 1]
        alias_keywords: list[str] = []
        alias_matches: list[tuple[int, int, tuple[str, ...]]] = []
        for chinese_term, aliases in _CHINESE_KEYWORD_ALIASES:
            alias_matches.extend(
                (match.start(), match.end(), aliases)
                for match in re.finditer(
                    re.escape(chinese_term),
                    normalized_text,
                )
            )
        for pattern, aliases in _NOISY_KEYWORD_ALIASES:
            alias_matches.extend(
                (match.start(), match.end(), aliases)
                for match in pattern.finditer(normalized_text)
            )
        occupied: set[int] = set()
        accepted_aliases: list[tuple[int, tuple[str, ...]]] = []
        for start, end, aliases in sorted(
            alias_matches,
            key=lambda item: (-(item[1] - item[0]), item[0]),
        ):
            if any(index in occupied for index in range(start, end)):
                continue
            occupied.update(range(start, end))
            accepted_aliases.append((start, aliases))
        for _start, aliases in sorted(accepted_aliases):
            alias_keywords.extend(aliases)
        seen: set[str] = set()
        unique: list[str] = []
        for w in keywords:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        # Cross-language aliases are the only signal that can match English
        # skill text from a Chinese request, so they get a reserved quota
        # instead of being truncated away behind 20 raw tokens.
        capped = unique[:_MAX_RAW_KEYWORDS]
        for alias in alias_keywords:
            if alias not in seen and len(capped) < _MAX_KEYWORDS:
                seen.add(alias)
                capped.append(alias)
        return capped

    def _estimate_complexity(
        self, text: str, intent: IntentType, keywords: list[str]
    ) -> str:
        length_score = min(len(text) / 200, 1.0)
        intent_scores = {
            IntentType.CODE: 0.6,
            IntentType.DEBUG: 0.5,
            IntentType.REFACTOR: 0.7,
            IntentType.EXPLAIN: 0.3,
            IntentType.SEARCH: 0.2,
            IntentType.REVIEW: 0.4,
            IntentType.TEST: 0.4,
            IntentType.DOCUMENT: 0.3,
            IntentType.CONFIGURE: 0.3,
            IntentType.QUESTION: 0.2,
            IntentType.CHAT: 0.1,
            IntentType.MEMORY: 0.1,
            IntentType.SYSTEM: 0.1,
            IntentType.UNKNOWN: 0.5,
        }
        intent_score = intent_scores.get(intent, 0.5)
        complex_keywords = {
            "architect",
            "design",
            "framework",
            "system",
            "platform",
            "infrastructure",
            "orchestrate",
            "pipeline",
            "migrate",
            "integrate",
            "refactor",
            "optimize",
            "performance",
        }
        keyword_score = sum(1 for k in keywords if k in complex_keywords) / max(
            len(keywords), 1
        )
        total = length_score * 0.2 + intent_score * 0.5 + keyword_score * 0.3
        if total < 0.3:
            return "simple"
        elif total < 0.6:
            return "moderate"
        return "complex"


_parser: IntentParser | None = None


def get_intent_parser() -> IntentParser:
    global _parser
    if _parser is None:
        _parser = IntentParser()
    return _parser


def parse_intent(
    user_input: str,
    *,
    context: IntentContext | None = None,
    semantic_adapter: IntentSemanticAdapter | None = None,
    call_context: Mapping[str, Any] | None = None,
) -> ParsedIntent:
    """Parse one turn without retaining conversation state between calls."""
    parser = (
        get_intent_parser()
        if semantic_adapter is None
        else IntentParser(semantic_adapter=semantic_adapter)
    )
    return parser.parse(
        user_input,
        context=context,
        call_context=call_context,
    )
