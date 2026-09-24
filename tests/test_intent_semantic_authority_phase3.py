"""A model proposal cannot turn source text or an explanation into authority."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentParser,
    IntentPolarity,
    IntentType,
    SemanticIntentProposal,
    SemanticIntentRequest,
)


_EFFECTS = frozenset({
    ActionType.CREATE, ActionType.UPDATE, ActionType.DELETE, ActionType.EXECUTE,
    ActionType.MOVE, ActionType.RENAME, ActionType.MERGE, ActionType.SPLIT,
})


class _FixedProposal:
    def __init__(
        self, intent: IntentType = IntentType.CODE,
        action: ActionType = ActionType.UPDATE,
    ) -> None:
        self.intent = intent
        self.action = action
        self.requests: list[SemanticIntentRequest] = []

    def infer(
        self, request: SemanticIntentRequest, **_kwargs: object,
    ) -> tuple[SemanticIntentProposal, ...]:
        self.requests.append(request)
        return (SemanticIntentProposal(
            intent_type=self.intent, action_type=self.action,
            polarity=IntentPolarity.AFFIRMED, confidence=0.85,
            clause="provider text cannot own request authority",
        ),)


@pytest.mark.parametrize("text", [
    "网页按钮的文案是“执行数据库迁移”。请解释“迁移”在这里是什么意思。",
    "控件的标签为“删除账号”。请解释标签含义。",
    "页面标题是“更新权限”。请说明标题的意思。",
    "输入框的占位符是“重写配置”。请解释这句话的含义。",
    'The button label is "Run database migration". Explain what migration means.',
    'The caption reads "Delete account". Explain the phrase.',
    'The warning message says "Update permissions". Describe the meaning.',
])
@pytest.mark.parametrize(("proposal_intent", "proposal_action"), [
    (IntentType.CODE, ActionType.UPDATE),
    (IntentType.CODE, ActionType.CREATE),
    (IntentType.TEST, ActionType.EXECUTE),
    (IntentType.CONFIGURE, ActionType.UPDATE),
])
def test_report_and_explanation_cannot_authorize_model_effect(
    text: str, proposal_intent: IntentType, proposal_action: ActionType,
) -> None:
    adapter = _FixedProposal(proposal_intent, proposal_action)
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert not any(o.action_type in _EFFECTS for o in parsed.affirmed_operations)
    assert all("数据库迁移" not in r.text and "Delete account" not in r.text
               and "Update permissions" not in r.text for r in adapter.requests)


@pytest.mark.parametrize("text", [
    "网页按钮的文案是“执行数据库迁移”。",
    "标签为“删除账号”。",
    'The button label is "Run database migration".',
    'The caption reads "Delete account".',
])
def test_pure_attribution_never_calls_the_model(text: str) -> None:
    adapter = _FixedProposal()
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests == []
    assert parsed.affirmed_operations == ()


@pytest.mark.parametrize("text", [
    "我只想知道“执行数据库迁移”的含义。",
    "请解释标签含义。",
    "Explain the phrase.",
    "Explain what migration means.",
])
def test_unknown_explanation_can_accept_only_the_reading_proposal(text: str) -> None:
    writer = _FixedProposal()
    reader = _FixedProposal(IntentType.EXPLAIN, ActionType.READ)
    blocked = IntentParser(semantic_adapter=writer).parse(text)
    accepted = IntentParser(semantic_adapter=reader).parse(text)

    assert writer.requests and reader.requests
    assert not any(o.action_type in _EFFECTS for o in blocked.affirmed_operations)
    assert accepted.is_read_only()
    assert any(o.intent_type is IntentType.EXPLAIN for o in accepted.affirmed_operations)


@pytest.mark.parametrize(("text", "real_request"), [
    ("按钮文案是“执行数据库迁移”。帮我换个更自然的说法。", "帮我换个更自然的说法。"),
    ('The label is "Delete account". Give it a friendlier wording.', "Give it a friendlier wording."),
    ("先解释“执行数据库迁移”的意思，然后把按钮调亮一点。", "把按钮调亮一点。"),
    ("把按钮（文案是“执行数据库迁移”）调亮一点。", None),
    ("给这个按钮换个说法。", None),
    ("帮我润色这段文案。", None),
    ("我想把标签换成“执行数据库迁移”。", None),
    ("确保按钮的文案是“删除账号”。", None),
    ("保证按钮文案是“删除账号”。", None),
    ("调整按钮文案为“执行数据库迁移”。", None),
    ("修复按钮，它的标签是“删除账号”。", None),
    ('The task is to ensure the label is "Delete account".', None),
    ('The desired label is "Delete account".', None),
    ("务必让按钮文案是“删除账号”。", None),
    ("Explain the phrase and give the button a friendlier wording.", None),
    ("Explain the label, then give it a friendlier wording.", None),
    ("解释标签的含义，并把按钮弄亮。", None),
])
def test_real_colloquial_write_request_keeps_semantic_authority(
    text: str, real_request: str | None,
) -> None:
    adapter = _FixedProposal()
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert len(adapter.requests) == 1
    if real_request is not None:
        assert adapter.requests[0].text == real_request
    assert any(o.action_type is ActionType.UPDATE for o in parsed.affirmed_operations)


@pytest.mark.parametrize("text", [
    "请检查标题是“执行数据库迁移”的按钮。",
    'Review the button whose label is "Delete account".',
    "解释标签的含义，然后修改 app.py。",
    'Explain the label, then update app.py.',
])
def test_attribution_inside_a_real_request_does_not_erase_it(text: str) -> None:
    parsed = IntentParser().parse(text)

    assert parsed.affirmed_operations
    if "app.py" in text:
        assert any(o.action_type is ActionType.UPDATE for o in parsed.affirmed_operations)
    else:
        assert parsed.is_read_only()
