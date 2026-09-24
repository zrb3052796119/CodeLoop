"""Request roles constrain rules and model proposals without erasing commands."""
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
    def __init__(self, intent: IntentType, action: ActionType) -> None:
        self.intent = intent
        self.action = action
        self.requests: list[SemanticIntentRequest] = []

    def infer(
        self, request: SemanticIntentRequest, **_kwargs: object,
    ) -> tuple[SemanticIntentProposal, ...]:
        self.requests.append(request)
        return (SemanticIntentProposal(
            intent_type=self.intent,
            action_type=self.action,
            polarity=IntentPolarity.AFFIRMED,
            confidence=0.85,
            clause="model-authored text cannot supply request authority",
        ),)


@pytest.mark.parametrize("text", [
    "这个类有点难维护。你觉得应该怎样重构？只讨论方案就好。",
    "那个命令会改动工作区里的文件吗？先解释一下它的影响。",
    "缓存文件会被这个脚本删除吗？",
    "我应该怎样重构这个类？先谈方案。",
    "为什么这个命令会修改文件？",
    "这个函数为什么会创建空目录？请解释。",
    "Does this command overwrite local files?",
    "Would the migration modify existing rows?",
    "How should we refactor this class?",
    "Should this command remove the cache?",
    "pytest 里的 -k 和 -m 分别有什么用？给我举例就行，不用真的运行测试。",
    "终端提示“请先运行 npm install，再执行 npm run dev”。这两步各自做什么？我还不准备执行。",
])
def test_questions_keep_positive_reading_authority(text: str) -> None:
    parsed = IntentParser().parse(text)

    assert parsed.affirmed_operations
    assert parsed.is_read_only()
    assert not any(o.action_type in _EFFECTS for o in parsed.affirmed_operations)
    assert any(o.action_type in {ActionType.READ, ActionType.ANALYZE}
               for o in parsed.affirmed_operations)


@pytest.mark.parametrize(("text", "action", "file"), [
    ("这个脚本会删除文件吗，另外请删除 tmp/cache.json？", ActionType.DELETE, "tmp/cache.json"),
    ("How should we refactor this class, and please update app.py?", ActionType.UPDATE, "app.py"),
    ("这个函数会修改状态吗，顺便帮我把 app.py 改成异步？", ActionType.UPDATE, "app.py"),
    ("这个脚本会删除文件吗（顺便删除 tmp/cache.json）？", ActionType.DELETE, "tmp/cache.json"),
    ("这个命令会修改文件吗？然后运行 pytest。", ActionType.EXECUTE, None),
    ("Explain how the command updates files, then update app.py.", ActionType.UPDATE, "app.py"),
    ("如果测试通过，就删除 tmp/cache.json。", ActionType.DELETE, "tmp/cache.json"),
    ("不用解释，直接重构这个类。", ActionType.UPDATE, None),
])
def test_new_commands_keep_their_action_and_target(
    text: str, action: ActionType, file: str | None,
) -> None:
    parsed = IntentParser().parse(text)
    commanded = [o for o in parsed.affirmed_operations if o.action_type is action]

    assert commanded
    if file is not None:
        assert any(file in o.entities.get("files", ()) for o in commanded)
    assert not parsed.is_read_only()


@pytest.mark.parametrize("text", [
    "能帮我重构这个类吗？",
    "Can you refactor this class?",
    "Could you update app.py?",
    "这个函数能否帮我重构一下？",
    "这个函数可否请你帮我重构一下？",
    "这个配置是不是可以帮我修改一下？",
    "该函数能否帮我改成异步？",
])
def test_polite_direct_or_object_fronted_requests_still_modify(text: str) -> None:
    parsed = IntentParser().parse(text)

    assert any(o.action_type is ActionType.UPDATE for o in parsed.affirmed_operations)


@pytest.mark.parametrize("text", [
    "Can this file please be updated?",
    "Could this function please be refactored?",
    "Would this script please be updated to support JSON?",
    "这个脚本是否可以请你修改一下？",
])
def test_polite_passive_requests_can_still_accept_model_update(text: str) -> None:
    adapter = _FixedProposal(IntentType.CODE, ActionType.UPDATE)
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests
    assert any(o.action_type is ActionType.UPDATE for o in parsed.affirmed_operations)


@pytest.mark.parametrize("text", [
    "只讨论方案就好。",
    "仅分析风险。",
    "Just discuss the plan.",
    "Please only assess the feasibility.",
    "先聊聊这些开关对输出的影响，不用实际执行测试。",
    "这俩选项是咋配合起来的啊，不必急着运行测试。",
])
@pytest.mark.parametrize("action", [ActionType.CREATE, ActionType.UPDATE])
def test_unresolved_discussion_rejects_wrong_model_write(
    text: str, action: ActionType,
) -> None:
    adapter = _FixedProposal(IntentType.CODE, action)
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    # This is an admission test: a rule bypass cannot make it pass vacuously.
    assert len(adapter.requests) == 1
    assert not any(o.action_type in _EFFECTS for o in parsed.affirmed_operations)


@pytest.mark.parametrize("text", [
    "只讨论方案就好。",
    "仅分析风险。",
    "Just discuss the plan.",
    "Please only assess the feasibility.",
    "先聊聊这些开关对输出的影响，不用实际执行测试。",
    "这俩选项是咋配合起来的啊，不必急着运行测试。",
])
def test_unresolved_discussion_accepts_model_read(text: str) -> None:
    adapter = _FixedProposal(IntentType.EXPLAIN, ActionType.READ)
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert len(adapter.requests) == 1
    assert parsed.is_read_only()
    assert any(o.intent_type is IntentType.EXPLAIN and o.action_type is ActionType.READ
               for o in parsed.affirmed_operations)


@pytest.mark.parametrize("text", [
    "帮我把这个工具弄得好用一点，不用实际执行测试。",
    "给这个按钮换个更自然的说法，不必急着运行测试。",
])
def test_negative_execution_constraint_keeps_real_colloquial_write(text: str) -> None:
    adapter = _FixedProposal(IntentType.CODE, ActionType.UPDATE)
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests
    assert any(o.action_type is ActionType.UPDATE for o in parsed.affirmed_operations)
    assert not any(o.action_type is ActionType.EXECUTE for o in parsed.affirmed_operations)


@pytest.mark.parametrize("text", [
    "说明文字换成英文。",
    "解释文字帮我润色一下。",
    "分析报告换成中文。",
    "说明文档给它换个更自然的说法。",
    "说明按钮换一个颜色。",
])
def test_read_verb_shaped_noun_does_not_block_a_real_edit(text: str) -> None:
    adapter = _FixedProposal(IntentType.CODE, ActionType.UPDATE)
    parsed = IntentParser(semantic_adapter=adapter).parse(text)

    assert len(adapter.requests) == 1
    assert any(o.action_type is ActionType.UPDATE for o in parsed.affirmed_operations)
