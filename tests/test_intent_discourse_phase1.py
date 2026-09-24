"""Phase-one intent contracts: mentioned actions never become extra commands."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentParser,
    IntentPolarity,
    IntentType,
    ParsedIntent,
    SemanticIntentProposal,
    SemanticIntentRequest,
    parse_intent,
)


def _has(
    intent: ParsedIntent,
    intent_type: IntentType,
    action_type: ActionType,
    polarity: IntentPolarity = IntentPolarity.AFFIRMED,
) -> bool:
    return any(
        operation.intent_type is intent_type
        and operation.action_type is action_type
        and operation.polarity is polarity
        for operation in intent.operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "配置文件放在哪个目录？帮我在仓库里找找。",
        "配置文件在哪里？请在项目中找一找。",
        "帮我在仓库里查找配置文件。",
    ],
)
def test_settings_file_is_a_search_object(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.SEARCH, ActionType.READ)
    assert intent.is_read_only() is True
    assert not _has(intent, IntentType.CONFIGURE, ActionType.UPDATE)


@pytest.mark.parametrize(
    "text",
    [
        "帮我审查这个 PR，重点看异常处理有没有遗漏。",
        "审查 upload.py，看看错误处理是否完整。",
        "审核 worker.py 的异常处理逻辑。",
        "审查 app.py 的异常处理。",
    ],
)
def test_exception_handling_is_the_review_subject(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.REVIEW
    assert intent.is_read_only() is True
    assert _has(intent, IntentType.REVIEW, ActionType.ANALYZE)
    assert not _has(intent, IntentType.DEBUG, ActionType.UPDATE)


@pytest.mark.parametrize(
    "text",
    [
        "更新 README.md，补上 Docker 部署步骤。",
        "更新 docs.md，添加服务器初始化说明。",
        "更新 README.md，补充环境配置步骤。",
    ],
)
def test_document_content_does_not_request_its_described_operation(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.DOCUMENT
    assert _has(intent, IntentType.DOCUMENT, ActionType.UPDATE)
    assert all(
        operation.intent_type is IntentType.DOCUMENT
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "刚才让你修复那个崩溃的问题先取消。现在只解释报错日志里“connection refused”的含义。",
        "刚才让你修复 app.py 的错误，这个请求取消了。当前只说明 app.py 的逻辑。",
        "修复 app.py 的错误这一步先取消。现在解释 app.py。",
    ],
)
def test_postposed_cancellation_revokes_repair_only(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.NEGATED)
    assert _has(intent, IntentType.EXPLAIN, ActionType.READ)
    assert intent.is_read_only() is True


@pytest.mark.parametrize("cue", ["撤回", "收回", "取消"])
def test_retracted_request_is_negative_and_following_review_stays_active(cue: str) -> None:
    intent = parse_intent(
        f"{cue}刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。"
    )

    assert _has(intent, IntentType.TEST, ActionType.CREATE, IntentPolarity.NEGATED)
    assert _has(intent, IntentType.REVIEW, ActionType.ANALYZE)
    assert not _has(intent, IntentType.TEST, ActionType.CREATE)
    assert intent.is_read_only() is True


@pytest.mark.parametrize(
    "text",
    [
        "不用搜索资料了，也别记住这段对话。直接解释一下 Python tuple 的不可变性。",
        "不要记住这段对话。现在只解释 auth.py。",
        "别记下这条信息。当前解释 config.py。",
    ],
)
def test_sentence_boundary_preserves_memory_denial_and_fresh_explanation(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.MEMORY, ActionType.CREATE, IntentPolarity.NEGATED)
    assert _has(intent, IntentType.EXPLAIN, ActionType.READ)
    assert not _has(intent, IntentType.EXPLAIN, ActionType.READ, IntentPolarity.NEGATED)
    assert intent.is_read_only() is True


@pytest.mark.parametrize(
    "text",
    [
        "只审查 cleanup.py，指出可能误删文件的地方。",
        "检查 cleanup.py，指出可能意外删除文件的地方。",
        "审查 cleanup.py 的错删文件风险。",
    ],
)
def test_accidental_deletion_is_a_review_subject(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.REVIEW, ActionType.ANALYZE)
    assert intent.is_read_only() is True
    assert not _has(intent, IntentType.CODE, ActionType.DELETE)


@pytest.mark.parametrize(
    "text",
    [
        "假设有人把 api.py 删了，请解释 Python 导入它时通常会发生什么。",
        "假如有人删除 cache.py，请解释这个模块的作用。",
        "假设有人删除 config.py，解释这个模块的影响。",
    ],
)
def test_hypothetical_deletion_only_supplies_explanation_subject(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.EXPLAIN, ActionType.READ)
    assert intent.is_read_only() is True
    assert not _has(intent, IntentType.CODE, ActionType.DELETE)
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"]


@pytest.mark.parametrize(
    ("text", "intent_type", "action_type"),
    [
        ("删除 temp.py。", IntentType.CODE, ActionType.DELETE),
        ("删除误写的文件。", IntentType.CODE, ActionType.DELETE),
        ("请修复 app.py 的异常处理。", IntentType.DEBUG, ActionType.UPDATE),
        ("修复 app.py 里订单取消的 bug。", IntentType.DEBUG, ActionType.UPDATE),
        ("配置服务器的环境变量。", IntentType.CONFIGURE, ActionType.UPDATE),
        ("配置文件中的超时参数请改成 30 秒。", IntentType.CODE, ActionType.UPDATE),
        ("如果测试通过就删除 temp.py。", IntentType.CODE, ActionType.DELETE),
        ("假如 tests 通过就删除 temp.py。", IntentType.CODE, ActionType.DELETE),
        ("记住默认用 Python。", IntentType.MEMORY, ActionType.CREATE),
        ("写单元测试。", IntentType.TEST, ActionType.CREATE),
    ],
)
def test_real_effectful_requests_remain_affirmed(
    text: str, intent_type: IntentType, action_type: ActionType
) -> None:
    intent = parse_intent(text)

    assert _has(intent, intent_type, action_type)
    assert not _has(intent, intent_type, action_type, IntentPolarity.NEGATED)


@pytest.mark.parametrize(
    "text",
    [
        "检查 cleanup.py 的误删风险，然后删除 temp.py。",
        "假设有人删除 api.py，然后删除 temp.py。",
        "不要删除 old.py。现在删除 temp.py。",
    ],
)
def test_later_direct_deletion_has_only_its_explicit_target(text: str) -> None:
    intent = parse_intent(text)
    deletions = [
        operation
        for operation in intent.affirmed_operations
        if operation.action_type is ActionType.DELETE
    ]

    assert len(deletions) == 1
    assert deletions[0].entities["files"] == ("temp.py",)


def test_document_description_does_not_mask_a_later_real_deployment() -> None:
    intent = parse_intent(
        "更新 README.md，补上 Docker 部署步骤，然后部署服务器。"
    )
    deployments = [
        operation
        for operation in intent.affirmed_operations
        if operation.intent_type is IntentType.CONFIGURE
    ]

    assert _has(intent, IntentType.DOCUMENT, ActionType.UPDATE)
    assert len(deployments) == 1
    assert deployments[0].clause == "部署服务器。"


def test_memory_prohibition_does_not_negate_next_sentence_repair() -> None:
    intent = parse_intent("不要记住这段对话。当前修复 app.py 的崩溃。")

    assert _has(intent, IntentType.MEMORY, ActionType.CREATE, IntentPolarity.NEGATED)
    assert _has(intent, IntentType.DEBUG, ActionType.UPDATE)


class _WriteProposingAdapter:
    """Adversarial provider response: local discourse must own admission."""

    def __init__(self) -> None:
        self.requests: list[SemanticIntentRequest] = []

    def infer(
        self, request: SemanticIntentRequest, **_kwargs: object
    ) -> tuple[SemanticIntentProposal, ...]:
        self.requests.append(request)
        return (
            SemanticIntentProposal(
                intent_type=IntentType.CODE,
                action_type=ActionType.UPDATE,
                polarity=IntentPolarity.AFFIRMED,
                confidence=0.8,
                clause=request.text,
            ),
        )


@pytest.mark.parametrize(
    "text",
    [
        "假设有人删除 api.py。",
        "假设有人再次删除 api.py。",
        "假设有人现在删除 api.py。",
        "假设有人删除 api.py，然后解释 config.py。",
        "假设有人删除 api.py，然后删除 temp.py。",
        '"delete api.py"',
        "日志里写着“删除 api.py”。",
        "刚才让你修复那个崩溃的问题先取消。现在只解释 app.py。",
        "撤回刚才让你写单元测试的请求。现在只审查 cleanup.py，指出可能误删文件的地方。",
        "只审查 cleanup.py，指出可能误删文件的地方。",
    ],
)
def test_semantic_fallback_cannot_reintroduce_inert_or_retracted_actions(text: str) -> None:
    adapter = _WriteProposingAdapter()
    intent = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests == []
    assert not _has(intent, IntentType.CODE, ActionType.UPDATE)
    effects = [
        operation for operation in intent.affirmed_operations
        if operation.action_type not in {ActionType.READ, ActionType.ANALYZE, ActionType.COMPARE}
    ]
    if "然后删除 temp.py" in text:
        assert len(effects) == 1
        assert effects[0].action_type is ActionType.DELETE
        assert effects[0].entities["files"] == ("temp.py",)
    else:
        assert effects == []


@pytest.mark.parametrize("prefix", ["", "假设有人删除 api.py；"])
def test_semantic_fallback_still_receives_the_unresolved_real_request(prefix: str) -> None:
    adapter = _WriteProposingAdapter()
    request = "给登录接口加限流功能。"
    text = prefix + request
    intent = IntentParser(semantic_adapter=adapter).parse(text)

    assert [value.text for value in adapter.requests] == [request]
    assert _has(intent, IntentType.CODE, ActionType.UPDATE)
    if prefix:
        assert intent.primary_operation is not None
        assert intent.primary_operation.span == (len(prefix), len(text))


@pytest.mark.parametrize(
    "text",
    [
        "请审查记住密码功能",
        "请解释记下来的信息",
        "记住密码的代码在哪里？",
        "我之前让你记住的代码风格偏好是什么？",
    ],
)
def test_memory_words_in_feature_names_and_history_do_not_request_memory_write(text: str) -> None:
    intent = parse_intent(text)

    assert not _has(intent, IntentType.MEMORY, ActionType.CREATE)
    assert not any(
        operation.action_type is ActionType.CREATE
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    ("text", "intent_type", "action_type", "target"),
    [
        ("假设测试通过，请修复 auth.py", IntentType.DEBUG, ActionType.UPDATE, "auth.py"),
        ("Suppose tests pass, please delete temp.py.", IntentType.CODE, ActionType.DELETE, "temp.py"),
    ],
)
def test_conditional_context_preserves_explicit_polite_command(
    text: str, intent_type: IntentType, action_type: ActionType, target: str
) -> None:
    adapter = _WriteProposingAdapter()
    intent = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests == []
    assert _has(intent, intent_type, action_type)
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == (target,)


@pytest.mark.parametrize(
    ("text", "real_request"),
    [
        ("误删了 auth.py，帮我恢复", "帮我恢复"),
        ("不小心删除了 auth.py，请帮我找回", "请帮我找回"),
    ],
)
def test_accident_report_does_not_swallow_unrecognized_recovery_request(
    text: str, real_request: str
) -> None:
    adapter = _WriteProposingAdapter()
    intent = IntentParser(semantic_adapter=adapter).parse(text)

    assert [value.text for value in adapter.requests] == [real_request]
    assert _has(intent, IntentType.CODE, ActionType.UPDATE)
    assert not _has(intent, IntentType.CODE, ActionType.DELETE)
    assert "auth.py" in intent.entities["files"]
    assert intent.primary_operation is not None
    assert intent.primary_operation.span == (text.index(real_request), len(text))


@pytest.mark.parametrize(
    "text",
    ["请审查撤回消息的代码", "请审查收回权限的代码"],
)
def test_retraction_feature_name_does_not_negate_review(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.REVIEW, ActionType.ANALYZE)
    assert intent.is_read_only() is True
    assert not _has(intent, IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.NEGATED)


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        (
            "工单标题是“重构缓存模块并补齐测试”。请解释这个标题涉及哪些工作，我想先弄懂它。",
            IntentType.EXPLAIN,
            ActionType.READ,
        ),
        (
            "同事留言“把 timeout 改成 60”。请审查这条建议是否有遗漏，重点说说风险。",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
    ],
)
def test_reported_quote_prefix_does_not_become_a_semantic_task(
    text: str, expected_intent: IntentType, expected_action: ActionType
) -> None:
    adapter = _WriteProposingAdapter()
    intent = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests == []
    assert _has(intent, expected_intent, expected_action)
    assert intent.is_read_only() is True


@pytest.mark.parametrize(
    "text",
    [
        "更新 README.md，添加 utils.py 中的函数来输出文件内容",
        "更新 README.md，添加函数打印日志内容",
    ],
)
def test_document_edit_does_not_override_new_code_behavior_request(text: str) -> None:
    intent = parse_intent(text)

    assert _has(intent, IntentType.DOCUMENT, ActionType.UPDATE)
    assert _has(intent, IntentType.CODE, ActionType.CREATE)
    assert len([
        operation for operation in intent.affirmed_operations
        if operation.intent_type is IntentType.DOCUMENT
    ]) == 1
