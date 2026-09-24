"""Current request verbs own authority; background/complement verbs do not."""

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


@pytest.mark.parametrize(
    "text",
    [
        "日志里的 `rename profile.json to profile.old.json` 只是应用打印出的建议。解释一下它想解决什么问题，先不要重命名或移动任何文件。",
        "说明这条建议希望修复什么问题。",
        "解释这个命令为什么要删除缓存文件。",
        "解释一下这段代码打算修改哪些文件。",
        "Explain what this command is trying to fix.",
        "Describe which files this script intends to delete.",
    ],
)
def test_explanation_complement_does_not_authorize_nested_action(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.EXPLAIN
    assert intent.is_read_only()
    assert any(o.action_type is ActionType.READ for o in intent.affirmed_operations)


@pytest.mark.parametrize(
    "text",
    [
        "运行 csv_importer.py 时，带 BOM 的表头导致第一列取不到值。帮我定位这个问题的原因，先给出诊断结论。",
        "执行 loader.py 的时候出现编码错误。请分析这个错误的原因。",
        "运行 app.py 期间会报错。请定位报错原因。",
        "When running importer.py, the first column is missing. Diagnose the cause.",
        "While executing app.py, it crashes. Diagnose the cause.",
    ],
)
def test_execution_circumstance_is_not_a_request_to_run(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.DEBUG
    assert intent.is_read_only()
    assert all(o.action_type is not ActionType.EXECUTE for o in intent.affirmed_operations)


def test_analysis_request_after_observed_failure_does_not_restart_execution() -> None:
    intent = parse_intent("运行 app.py 时出现了错误，请分析原因。")

    assert intent.is_read_only()
    assert intent.affirmed_operations
    assert all(o.action_type is not ActionType.EXECUTE for o in intent.affirmed_operations)


@pytest.mark.parametrize(
    ("text", "required"),
    [
        ("运行 csv_importer.py 复现这个问题，然后修复编码错误。", ActionType.EXECUTE),
        ("请运行 app.py，观察是否报错。", ActionType.EXECUTE),
        ("运行 app.py 时记录日志。", ActionType.EXECUTE),
        ("运行 app.py 时记录所有失败请求。", ActionType.EXECUTE),
        ("运行 app.py 时如果失败立即退出。", ActionType.EXECUTE),
        ("运行 app.py 时收集失败请求的堆栈。", ActionType.EXECUTE),
        ("运行 app.py 时如果失败发送诊断信息。", ActionType.EXECUTE),
        ("运行 app.py 期间发生错误立刻记录日志。", ActionType.EXECUTE),
        ("如果运行 app.py 时报错，告诉我。", ActionType.EXECUTE),
        ("运行 app.py 时如果报错就停止。", ActionType.EXECUTE),
        ("运行 app.py 的时候，如果出现错误就保存日志。", ActionType.EXECUTE),
        ("运行 app.py 时出现错误要重试。", ActionType.EXECUTE),
        ("执行 import.py 时发生冲突就跳过当前行。", ActionType.EXECUTE),
        ("运行 app.py 时，如果失败，请退出。", ActionType.EXECUTE),
        ("运行 app.py 时，如果失败，保存日志。", ActionType.EXECUTE),
        ("运行 app.py 时出现错误，请保存日志。", ActionType.EXECUTE),
        ("Run app.py when the service is ready.", ActionType.EXECUTE),
        ("解释 app.py 的作用，然后运行 app.py。", ActionType.EXECUTE),
        ("Explain app.py, then run app.py.", ActionType.EXECUTE),
        ("解释 app.py 的逻辑，然后修复它的错误。", ActionType.UPDATE),
        ("如果运行 app.py 时出现错误，就修复 app.py。", ActionType.UPDATE),
    ],
)
def test_present_execution_and_following_commands_keep_authority(
    text: str, required: ActionType
) -> None:
    intent = parse_intent(text)

    assert any(o.action_type is required for o in intent.affirmed_operations)


class _WriteProposingAdapter:
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
        "日志里的 `rename profile.json to profile.old.json` 只是应用打印出的建议。",
        "控制台中的 `delete cache.py` 只是程序输出的提示。",
        "运行 importer.py 时，表头导致第一列取不到值。",
        "When running importer.py, the first column is missing.",
        "日志里的 `rename profile.json to profile.old.json` 只是应用打印出的建议。解释一下它想解决什么问题，先不要重命名或移动任何文件。",
    ],
)
def test_background_is_not_reoffered_to_semantic_fallback(text: str) -> None:
    adapter = _WriteProposingAdapter()
    intent = IntentParser(semantic_adapter=adapter).parse(text)

    assert adapter.requests == []
    assert not any(o.action_type is ActionType.UPDATE for o in intent.affirmed_operations)


def test_real_command_after_reported_advice_remains_actionable() -> None:
    intent = parse_intent(
        "日志里的 `rename old.py to new.py` 只是应用打印出的建议。现在删除 temp.py。"
    )
    deletions = [o for o in intent.affirmed_operations if o.action_type is ActionType.DELETE]

    assert len(deletions) == 1
    assert deletions[0].entities["files"] == ("temp.py",)


@pytest.mark.parametrize(
    "text",
    [
        "暂时不要重构 cache.py，也不要生成测试；只阅读现有代码，分析缓存命中率下降的可能原因。",
        "阅读当前实现，分析请求成功率下降的原因。",
        "只阅读代码，分析接口延迟上升的原因。",
    ],
)
def test_causal_diagnosis_owns_primary_intent_after_reading(text: str) -> None:
    intent = parse_intent(text)

    assert intent.intent_type is IntentType.DEBUG
    assert intent.action_type is ActionType.ANALYZE
    assert intent.is_read_only()


@pytest.mark.parametrize(
    "text",
    [
        "Investigate the cause of the plant leaves turning yellow.",
        "Diagnose the cause of the dog's behavior.",
        "Investigate the cause of the late delivery.",
        "分析招聘成功率下降的原因。",
        "分析列车延迟的原因。",
        "分析谈判失败的原因。",
    ],
)
def test_generic_cause_request_without_software_is_not_debug(text: str) -> None:
    intent = parse_intent(text)

    assert not any(o.intent_type is IntentType.DEBUG for o in intent.affirmed_operations)
    assert intent.intent_type is IntentType.EXPLAIN
    assert intent.is_read_only()


@pytest.mark.parametrize(
    "text",
    [
        "给 receipt_parser.py 增加识别电子发票号码的逻辑，并在 docs/receipts.md 里补上支持范围和输入例子。",
        "为 loader.py 增加读取新格式的功能，并更新 README.md。",
        "在 service.py 中新增重试逻辑，并更新 docs/api.md。",
    ],
)
def test_existing_source_extension_and_document_update_both_survive(text: str) -> None:
    intent = parse_intent(text)
    pairs = {(o.intent_type, o.action_type) for o in intent.affirmed_operations}

    assert (IntentType.CODE, ActionType.UPDATE) in pairs
    assert (IntentType.DOCUMENT, ActionType.UPDATE) in pairs


@pytest.mark.parametrize("verb", ["移到", "挪到", "移动到"])
def test_explicit_file_destinations_supply_a_move_request(verb: str) -> None:
    intent = parse_intent(f"把 assets/logo.svg {verb} archive/logo.svg，保留文件内容。")

    assert intent.action_type is ActionType.MOVE
    assert any(o.action_type is ActionType.MOVE for o in intent.affirmed_operations)


@pytest.mark.parametrize("prefix", ["不要", "别", "禁止"])
def test_explicit_destination_does_not_override_move_denial(prefix: str) -> None:
    intent = parse_intent(f"{prefix}把 assets/logo.svg 移到 archive/logo.svg。")

    assert not intent.affirmed_operations
    assert any(
        o.action_type is ActionType.MOVE and o.polarity is IntentPolarity.NEGATED
        for o in intent.operations
    )
