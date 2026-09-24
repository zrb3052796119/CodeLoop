"""Independent request boundaries preserve positive work and local denials."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentParser,
    IntentPolarity,
    IntentType,
    SemanticIntentProposal,
    SemanticIntentRequest,
    parse_intent,
)


_EFFECTS = frozenset({
    ActionType.CREATE, ActionType.UPDATE, ActionType.DELETE, ActionType.EXECUTE,
    ActionType.MOVE, ActionType.RENAME, ActionType.MERGE, ActionType.SPLIT,
})


class _ReadProposal:
    def __init__(self) -> None:
        self.requests: list[SemanticIntentRequest] = []

    def infer(
        self, request: SemanticIntentRequest, **_kwargs: object,
    ) -> tuple[SemanticIntentProposal, ...]:
        self.requests.append(request)
        return (SemanticIntentProposal(
            intent_type=IntentType.EXPLAIN,
            action_type=ActionType.READ,
            polarity=IntentPolarity.AFFIRMED,
            confidence=0.85,
            clause="The provider cannot own spans or polarity.",
        ),)


@pytest.mark.parametrize("text", [
    "pytest 里的 -k 和 -m 分别有什么用？给我举例就行，不用真的运行测试。",
    "先聊聊这些开关对输出的影响，不用实际执行测试。",
    "这俩选项是咋配合起来的啊，不必急着运行测试。",
    "终端提示“请先运行 npm install，再执行 npm run dev”。这两步各自做什么？我还不准备执行。",
    "这段说明得怎么看呀？我暂时不打算运行测试。",
])
def test_an_independent_denial_does_not_cover_the_positive_read_request(
    text: str,
) -> None:
    adapter = _ReadProposal()
    result = IntentParser(semantic_adapter=adapter).parse(text)

    assert any(op.action_type in {ActionType.READ, ActionType.ANALYZE}
               for op in result.affirmed_operations)
    assert result.is_read_only()
    assert any(op.action_type is ActionType.EXECUTE for op in result.negated_operations)
    # Fallback, when needed, must classify the positive request. A rule may
    # instead resolve a complete question without calling the provider.
    for request in adapter.requests:
        assert all(denial not in request.text for denial in (
            "不用真的运行", "不用实际执行", "不必急着运行",
            "不准备执行", "不打算运行",
        ))


@pytest.mark.parametrize("text", [
    "如果把数据库连接池上限从 20 改成 100，会有哪些性能影响？先帮我分析，不要调整配置。",
    "如果重构这个模块，会增加哪些风险？先帮我分析，不用实际重构。",
    "如果删除 cache.py，会丢失哪些内容？先帮我分析，不要删除文件。",
])
def test_a_following_analysis_request_survives_a_hypothetical_and_denial(
    text: str,
) -> None:
    result = parse_intent(text)

    assert any(op.action_type in {ActionType.READ, ActionType.ANALYZE}
               for op in result.affirmed_operations)
    assert not any(op.action_type in _EFFECTS for op in result.affirmed_operations)
    assert result.negated_operations


@pytest.mark.parametrize(("text", "affirmed", "denied"), [
    ("不用解释，直接重构这个类。", ActionType.UPDATE, ActionType.READ),
    ("无需分析，立即优化查询逻辑。", ActionType.UPDATE, ActionType.READ),
    ("不需要说明，马上调整日志配置。", ActionType.UPDATE, ActionType.READ),
    ("无需重构，直接修改 app.py。", ActionType.UPDATE, ActionType.UPDATE),
    ("不用实际运行测试，更新 README.md。", ActionType.UPDATE, ActionType.EXECUTE),
    ("不准备运行测试，先解释 app.py。", ActionType.READ, ActionType.EXECUTE),
    ("我不打算删除 cache.py，更新 README.md。", ActionType.UPDATE, ActionType.DELETE),
])
def test_an_explicit_current_command_is_not_cancelled_by_a_prior_denial(
    text: str, affirmed: ActionType, denied: ActionType,
) -> None:
    result = parse_intent(text)

    assert any(op.action_type is affirmed for op in result.affirmed_operations)
    assert any(op.action_type is denied for op in result.negated_operations)


@pytest.mark.parametrize("text", [
    "不要先运行测试再修改代码。",
    "不用先解释再重构这个类。",
    "不需要先分析然后修改 app.py。",
    "不用先说明随后修改 app.py。",
    "不要直接重构这个类。",
    "我还不准备运行测试。",
    "我暂时不打算删除 cache.py。",
    "解释字符串“别解释，直接修改 app.py”。",
    "Explain the text 'Do not explain; directly modify app.py'.",
])
def test_a_single_negated_sequence_or_quoted_request_never_grants_write(
    text: str,
) -> None:
    result = parse_intent(text)

    assert not any(op.action_type in _EFFECTS for op in result.affirmed_operations)


@pytest.mark.parametrize(("text", "action"), [
    ("如果测试通过就删除 cache.py。", ActionType.DELETE),
    ("如果需要，直接删除 cache.py。", ActionType.DELETE),
    ("如果支持的话，请重构这个类。", ActionType.UPDATE),
    ("先不要解释，然后重构这个类。", ActionType.UPDATE),
    ("不用先解释，然后请修改 app.py。", ActionType.UPDATE),
    ("不用先解释，随后直接修改 app.py。", ActionType.UPDATE),
])
def test_real_conditional_commands_and_punctuated_resets_keep_authority(
    text: str, action: ActionType,
) -> None:
    result = parse_intent(text)

    assert any(op.action_type is action for op in result.affirmed_operations)
