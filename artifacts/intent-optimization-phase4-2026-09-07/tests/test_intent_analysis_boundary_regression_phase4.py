"""A factual report stays attached to the analysis it supplies a subject for."""

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


class _DiagnosisProposal:
    def __init__(self) -> None:
        self.requests: list[SemanticIntentRequest] = []

    def infer(
        self, request: SemanticIntentRequest, **_kwargs: object,
    ) -> tuple[SemanticIntentProposal, ...]:
        self.requests.append(request)
        return (SemanticIntentProposal(
            intent_type=IntentType.DEBUG,
            action_type=ActionType.ANALYZE,
            polarity=IntentPolarity.AFFIRMED,
            confidence=0.85,
            clause="provider text does not own the operation span",
        ),)


@pytest.mark.parametrize(("report", "analysis", "denial"), [
    (
        "同一次支付回调偶尔会生成两张收据，日志里两条请求时间差不到一毫秒。",
        "先帮我分析是不是幂等检查有竞态",
        "不要碰实现。",
    ),
    (
        "同一个包偶尔会写出重复条目，两个写入者持有相同序号。",
        "先分析是不是持久化步骤缺少同步",
        "不要碰实现。",
    ),
    (
        "同一份任务结果偶尔会丢失，记录显示确认和保存的顺序相反。",
        "先麻烦分析是不是确认得太早",
        "不要调整配置。",
    ),
])
def test_factual_context_and_analysis_reach_one_semantic_request(
    report: str, analysis: str, denial: str,
) -> None:
    adapter = _DiagnosisProposal()
    parsed = IntentParser(semantic_adapter=adapter).parse(
        f"{report}{analysis}，{denial}"
    )

    assert len(adapter.requests) == 1
    assert adapter.requests[0].text == f"{report}{analysis}"
    assert parsed.intent_type is IntentType.DEBUG
    assert any(op.action_type is ActionType.ANALYZE for op in parsed.affirmed_operations)
    assert parsed.is_read_only()
    assert any(op.action_type is ActionType.UPDATE for op in parsed.negated_operations)


@pytest.mark.parametrize("text", [
    "如果把数据库连接池上限从 20 改成 100，会有哪些性能影响？先帮我分析，不要调整配置。",
    "假设删除 cache.py，会丢失哪些内容？先分析，不要删除文件。",
    "如果重构这个模块，会增加哪些风险？先帮我分析，不用实际重构。",
])
def test_hypothetical_actions_do_not_swallow_the_following_positive_analysis(
    text: str,
) -> None:
    parsed = parse_intent(text)

    assert any(op.action_type in {ActionType.READ, ActionType.ANALYZE}
               for op in parsed.affirmed_operations)
    assert not any(op.action_type in _EFFECTS for op in parsed.affirmed_operations)
    assert parsed.negated_operations


@pytest.mark.parametrize("text", [
    "不要修改 app.py。先帮我分析代码。",
    "不用先分析然后修改 app.py。",
    "不用先分析随后删除 cache.py。",
])
def test_a_denied_write_does_not_gain_authority_from_adjacent_analysis(
    text: str,
) -> None:
    parsed = parse_intent(text)

    assert not any(op.action_type in _EFFECTS for op in parsed.affirmed_operations)
    assert any(op.action_type in _EFFECTS for op in parsed.negated_operations)


@pytest.mark.parametrize(("text", "action", "target"), [
    (
        "支付回调偶尔重复触发。先帮我分析原因，然后修改 app.py。",
        ActionType.UPDATE,
        "app.py",
    ),
    ("如果测试通过就删除 cache.py。", ActionType.DELETE, "cache.py"),
    ("不用先分析，然后请修改 app.py。", ActionType.UPDATE, "app.py"),
])
def test_an_independent_or_conditional_write_keeps_its_action_and_target(
    text: str, action: ActionType, target: str,
) -> None:
    parsed = parse_intent(text)

    assert any(op.action_type is action and target in op.entities["files"]
               for op in parsed.affirmed_operations)
