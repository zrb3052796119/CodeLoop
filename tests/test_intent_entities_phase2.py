"""Public parser regressions for file literals and bounded file references."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentType,
    parse_intent,
)


def _context(*messages: str, active_task: str = "") -> IntentContext:
    return IntentContext.from_messages(
        [{"role": "user", "content": message} for message in messages],
        active_task=active_task,
    )


@pytest.mark.parametrize(
    "target",
    [
        "gateway.log",
        "export_dump.csv",
        "assets/old-logo.svg",
        ".coveragerc",
        "config/.env.local",
        "src/订单.py",
        "报告.md",
        "src/账户数据_v2.py",
    ],
)
def test_read_operation_retains_common_project_file_literals(target: str) -> None:
    intent = parse_intent(f"请审查文件 {target} 的内容。")

    assert intent.is_read_only()
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == (target,)
    assert intent.entities["files"] == [target]


def test_log_search_retains_file_adjacent_to_chinese_prose() -> None:
    intent = parse_intent("请从gateway.log中找出包含 timeout 的记录。")

    assert intent.intent_type is IntentType.SEARCH
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("gateway.log",)
    assert intent.entities["files"] == ["gateway.log"]


def test_quoted_unicode_file_is_still_an_explicit_current_target() -> None:
    intent = parse_intent("更新 `报告.md` 的示例。")

    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("报告.md",)
    assert intent.entities["files"] == ["报告.md"]


@pytest.mark.parametrize(
    ("reference", "target"),
    [
        ("后一个文件", "payment.py"),
        ("前一个文件", "order.py"),
        ("第二个文件", "payment.py"),
        ("第一个文件", "order.py"),
    ],
)
def test_pair_reference_selects_one_review_target(reference: str, target: str) -> None:
    intent = parse_intent(
        f"继续审查{reference}有没有重复扣款的风险。",
        context=_context("请帮我审查 order.py 和 payment.py 的实现。"),
    )

    assert intent.intent_type is IntentType.REVIEW
    assert intent.is_read_only()
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == (target,)
    assert intent.entities["files"] == [target]
    assert intent.context_used


@pytest.mark.parametrize(
    ("reference", "target"),
    [
        ("第一个文件", "order.py"),
        ("第二个文件", "payment.py"),
        ("the last file", "refund.py"),
    ],
)
def test_explicit_index_can_select_from_three_files(
    reference: str, target: str
) -> None:
    intent = parse_intent(
        (
            f"Review {reference}."
            if reference == "the last file"
            else f"继续审查{reference}有没有重复扣款的风险。"
        ),
        context=_context("审查 order.py、payment.py 和 refund.py。"),
    )

    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == (target,)
    assert intent.entities["files"] == [target]


@pytest.mark.parametrize(
    ("history", "reference"),
    [
        ("审查 order.py 和 payment.py", "这份文件"),
        ("审查 order.py、payment.py 和 refund.py", "后一个文件"),
    ],
)
def test_ambiguous_file_reference_does_not_inherit_all_candidates(
    history: str, reference: str
) -> None:
    intent = parse_intent(
        f"继续审查{reference}有没有重复扣款的风险。",
        context=_context(history),
    )

    assert intent.intent_type is IntentType.REVIEW
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ()
    assert intent.entities["files"] == []


def test_current_explicit_file_overrides_a_prior_pair() -> None:
    intent = parse_intent(
        "继续审查 invoice.py 有没有重复扣款的风险。",
        context=_context("审查 order.py 和 payment.py"),
    )

    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("invoice.py",)
    assert intent.entities["files"] == ["invoice.py"]


@pytest.mark.parametrize("value", ["None", "空字符串", "超长字符串"])
def test_additional_test_case_binds_unique_subject_file(value: str) -> None:
    intent = parse_intent(
        f"再加一个 {value} 输入的用例。",
        context=_context("请给 utils.py 中的 normalize_name 加边界情况测试。"),
    )

    assert intent.intent_type is IntentType.TEST
    assert intent.action_type in {ActionType.CREATE, ActionType.UPDATE}
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("utils.py",)
    assert intent.entities["files"] == ["utils.py"]


@pytest.mark.parametrize(
    "history",
    [
        "请给 utils.py 和 parser.py 加边界情况测试。",
        "给 utils.py 和 parser.py 添加测试。旧结果保存在 reports/old.log。",
        "Add tests for utils.py and parser.py. Old results are in reports/old.log.",
    ],
)
def test_additional_test_case_does_not_choose_between_two_subjects(
    history: str,
) -> None:
    intent = parse_intent(
        "再加一个 None 输入的用例。",
        context=_context(history),
    )

    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ()
    assert intent.entities["files"] == []


def test_old_delete_authority_cannot_supply_new_test_write_target() -> None:
    intent = parse_intent(
        "再加一个 None 输入的用例。",
        context=_context("删除 archived.py"),
    )

    assert all(
        not operation.entities.get("files") for operation in intent.affirmed_operations
    )
    assert "archived.py" not in intent.entities["files"]
    assert all(
        operation.action_type is not ActionType.DELETE
        for operation in intent.affirmed_operations
    )


def test_canceled_delete_object_can_be_explained_without_write_authority() -> None:
    intent = parse_intent(
        "先别删，取消刚才的要求。只解释为什么还需要保留这份导出快照。",
        context=_context("把 export_dump.csv 删除，已经有数据库备份了。"),
    )

    assert intent.is_read_only()
    assert intent.intent_type is IntentType.EXPLAIN
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("export_dump.csv",)
    assert intent.entities["files"] == ["export_dump.csv"]


def test_explained_retracted_literal_survives_top_level_entity_projection() -> None:
    intent = parse_intent(
        "我撤回“给 import_jobs.py 加自动重试”的要求。"
        "先说明任务失败后直接退出可能有哪些后果，不要改这份文件。"
    )

    assert intent.is_read_only()
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("import_jobs.py",)
    assert intent.entities["files"] == ["import_jobs.py"]


def test_a_reported_file_cannot_override_current_review_target() -> None:
    intent = parse_intent(
        "日志原样写着“删除 archive/old-logo.svg”；请审查 router.py 的逻辑。"
    )

    assert intent.is_read_only()
    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("router.py",)
    assert intent.entities["files"] == ["router.py"]


def test_active_task_wins_over_older_file_list() -> None:
    intent = parse_intent(
        "请审查这份文件有没有重复扣款的风险。",
        context=_context("审查 order.py 和 payment.py", active_task="审查 invoice.py"),
    )

    assert "order.py" not in intent.entities["files"]
    assert "payment.py" not in intent.entities["files"]


def test_context_with_changed_domain_does_not_bind_an_old_file() -> None:
    intent = parse_intent(
        "请审查这份文件。",
        context=_context("请审查 auth.py", "换个话题，周末的家庭旅行怎么安排？"),
    )

    assert "auth.py" not in intent.entities["files"]
    assert all(
        "auth.py" not in operation.entities.get("files", ())
        for operation in intent.affirmed_operations
    )


@pytest.mark.parametrize(
    "history",
    [
        "给当前模块添加测试。旧测试结果保存在 reports/old.log。",
        "给当前模块添加测试；旧测试结果保存在 reports/old.log。",
        "给当前模块添加测试，错误信息参考 reports/old.log。",
        "给当前模块添加测试并将结果保存在 reports/old.log。",
        "给当前模块添加测试。参考 `utils.py` 的旧输出。",
        "Add tests for the current module. Old results are in reports/old.log.",
        "给 legacy.py 不要添加测试；给当前模块添加测试。",
        "给 legacy.py 添加测试的要求已经取消；给当前模块添加测试。",
    ],
)
def test_background_file_never_becomes_an_additional_test_write_target(
    history: str,
) -> None:
    intent = parse_intent("再加一个 None 输入的用例。", context=_context(history))

    assert intent.primary_operation is not None
    assert intent.intent_type is IntentType.TEST
    assert intent.primary_operation.entities["files"] == ()
    assert intent.entities["files"] == []


@pytest.mark.parametrize(
    "history",
    [
        "Add tests for utils.py",
        "给 utils.py 添加边界测试。旧测试结果保存在 reports/old.log。",
        "为 utils.py 添加边界测试；旧测试结果保存在 reports/old.log。",
        "给 utils.py 添加边界测试，错误信息参考 reports/old.log。",
        "Add tests for utils.py. Old results are in reports/old.log.",
    ],
)
def test_explicit_test_subject_remains_distinct_from_background_files(
    history: str,
) -> None:
    intent = parse_intent("再加一个 None 输入的用例。", context=_context(history))

    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("utils.py",)
    assert intent.entities["files"] == ["utils.py"]


def test_revoked_test_target_is_not_unioned_with_the_current_explicit_subject() -> None:
    intent = parse_intent(
        "再加一个 None 输入的用例。",
        context=_context(
            "给 legacy.py 添加测试的要求已经取消；给 utils.py 添加边界测试。"
        ),
    )

    assert intent.primary_operation is not None
    assert intent.primary_operation.entities["files"] == ("utils.py",)
    assert intent.entities["files"] == ["utils.py"]
