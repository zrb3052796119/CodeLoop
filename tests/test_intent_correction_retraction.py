from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    parse_intent,
)


def _signatures(
    text: str,
) -> list[tuple[IntentType, ActionType, IntentPolarity]]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in parse_intent(text).operations
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "把模块删——算了，不删，只解释它",
            [
                (IntentType.CODE, ActionType.DELETE, IntentPolarity.NEGATED),
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "rename package—scratch that—just explain its imports",
            [
                (IntentType.CODE, ActionType.RENAME, IntentPolarity.NEGATED),
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "写 README，等等，只给我一个口头概要，别落盘",
            [
                (
                    IntentType.DOCUMENT,
                    ActionType.CREATE,
                    IntentPolarity.NEGATED,
                ),
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "run pytest, actually don't run it; inspect the traceback only",
            [
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
                (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
    ],
    ids=["zh-delete", "en-rename", "zh-document", "en-execute"],
)
def test_correction_retracts_preceding_operation_and_parses_replacement(
    text: str,
    expected: list[tuple[IntentType, ActionType, IntentPolarity]],
) -> None:
    assert _signatures(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "删除 auth.py——算了",
            [(IntentType.CODE, ActionType.DELETE, IntentPolarity.NEGATED)],
        ),
        (
            "write README.md; wait, explain the module instead",
            [
                (
                    IntentType.DOCUMENT,
                    ActionType.CREATE,
                    IntentPolarity.NEGATED,
                ),
                (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "修改 auth.py。哦不，还是只审查它。",
            [
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
            ],
        ),
    ],
    ids=["cancel-only", "wait", "zh-correction"],
)
def test_retracted_mutations_remain_as_explicit_safety_constraints(
    text: str,
    expected: list[tuple[IntentType, ActionType, IntentPolarity]],
) -> None:
    intent = parse_intent(text)

    assert _signatures(text) == expected
    assert all(
        any(code.startswith("explicit:") for code in operation.evidence_codes)
        for operation in intent.negated_operations
    )


@pytest.mark.parametrize(
    "text",
    [
        "Review auth.py. Nah, scratch that—explain auth.py instead.",
        "审查 auth.py。哦不，还是解释 auth.py。",
    ],
)
def test_retracted_read_only_operation_is_dropped_instead_of_becoming_a_denial(
    text: str,
) -> None:
    assert _signatures(text) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Run pytest—scratch that—review the pytest failures instead.",
        "跑 pytest——算了，只看 pytest 的失败原因",
    ],
    ids=["en-related-test-output", "zh-related-test-output"],
)
def test_retracted_execution_remains_a_constraint_for_related_test_analysis(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert _signatures(text) == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
        (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
    ]
    assert intent.operations[0].evidence_codes == (
        "explicit:retracted-test.execute",
    )


def test_latest_correction_can_reverse_a_read_only_denial() -> None:
    assert _signatures("别审安全，哦不，还是审吧，重点看越权") == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]

    intent = parse_intent(
        "别用 security review——等等，就是用它，只读审计"
    )
    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]
    assert "security" in intent.operations[0].keywords


def test_discourse_words_without_a_preceding_request_do_not_cancel_work() -> None:
    assert _signatures("Actually, run pytest.") == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED)
    ]


def test_chinese_etcetera_is_not_treated_as_a_retraction() -> None:
    signatures = _signatures("写 README，等等这些内容都要覆盖")

    assert signatures == [
        (IntentType.DOCUMENT, ActionType.CREATE, IntentPolarity.AFFIRMED)
    ]


def test_bare_submission_is_only_understood_as_commit_from_diff_correction() -> None:
    intent = parse_intent("提交吧，哦不，先只看 diff")

    assert _signatures(intent.raw_input) == [
        (IntentType.CODE, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
    ]
    assert "commit" in intent.operations[0].keywords
    assert intent.operations[0].evidence_codes == (
        "explicit:retracted-code.update",
    )


def test_dependency_pin_review_reuses_only_retracted_subject_not_write_authority() -> None:
    intent = parse_intent(
        "升级 package，不，先审下当前 pin，暂时别升"
    )

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.CONFIGURE, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
    ]
    assert "dependency pin" in intent.operations[1].keywords
    assert not any(
        operation.intent_type is IntentType.CONFIGURE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


@pytest.mark.parametrize("cue", ["撤回", "收回"])
def test_spoken_retraction_cues_revoke_write_before_read_only_review(
    cue: str,
) -> None:
    intent = parse_intent(
        f"直接修 SQL 注入……{cue}，只读审计注入点"
    )

    assert [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ] == [
        (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.NEGATED),
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
    ]
    assert intent.operations[0].evidence_codes == (
        "explicit:retracted-debug.update",
    )


def test_correction_to_read_only_security_check_resets_local_negation() -> None:
    intent = parse_intent(
        "先修 auth 越权——等等，不改了，只做只读安全检查"
    )

    assert any(
        operation.intent_type is IntentType.REVIEW
        and operation.action_type is ActionType.ANALYZE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in {ActionType.CREATE, ActionType.UPDATE}
        for operation in intent.operations
    )
