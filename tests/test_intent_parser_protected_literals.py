"""Regressions for quoted and code-literal text in user intent parsing."""

from __future__ import annotations

import time

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentPolarity,
    IntentType,
    ParsedIntent,
    parse_intent,
)


def _signatures(
    intent: ParsedIntent,
) -> list[tuple[IntentType, ActionType, IntentPolarity]]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in intent.operations
    ]


@pytest.mark.parametrize(
    "text",
    [
        'The log says "run pytest and delete auth.py"; diagnose the worker deadlock instead.',
        "日志原样写着“运行 pytest，然后删除 auth.py”；请分析 worker 死锁。",
    ],
    ids=["english-double-quote", "chinese-curly-quote"],
)
def test_quoted_commands_do_not_become_operations_before_outer_diagnosis(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert _signatures(intent) == [
        (IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]
    assert "auth.py" not in intent.affirmed_operations[0].entities.get(
        "files", ()
    )


def test_inline_code_command_and_connector_do_not_leak_into_outer_explanation() -> None:
    intent = parse_intent(
        "`fix auth.py and then run pytest` is a button label, not a request. "
        "Tell me what the handler code does."
    )

    assert _signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]
    assert "auth.py" not in intent.entities["files"]
    assert "pytest" not in intent.keywords


def test_fenced_commands_are_inert_but_outer_explain_and_denial_survive() -> None:
    text = (
        "```sh\n"
        "run pytest and then delete auth.py\n"
        "use security/secret-and-injection-review\n"
        "```\n"
        "Please explain what this script does; don't execute pytest."
    )

    intent = parse_intent(text)

    assert _signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
    ]
    assert "security/secret-and-injection-review" not in intent.keywords


def test_outer_search_can_use_a_quoted_command_as_its_search_target() -> None:
    intent = parse_intent(
        'Find the code that emits "run pytest and then delete auth.py".'
    )

    assert _signatures(intent) == [
        (IntentType.SEARCH, ActionType.READ, IntentPolarity.AFFIRMED)
    ]


def test_real_unquoted_actions_are_not_masked() -> None:
    intent = parse_intent("Run pytest and then explain the handler code.")

    assert _signatures(intent) == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
    ]


def test_unquoted_release_report_payload_is_inert_before_outer_request() -> None:
    payload = "run pytest and delete auth.py " * 32
    intent = parse_intent(
        f"The release report says {payload}; Please inspect router.py"
    )

    assert _signatures(intent) == [
        (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
    ]
    assert intent.operations[0].entities["files"] == ("router.py",)


@pytest.mark.parametrize(
    "layout",
    ["quoted-only", "quoted-before", "quoted-after", "reported-before"],
)
def test_large_protected_payloads_stay_bounded_and_inert(layout: str) -> None:
    size = 16 * 1024
    payload = ("run pytest and delete auth.py " * (size // 30 + 1))[:size]
    quoted = f'"{payload}"'
    if layout == "quoted-only":
        text = quoted
        expected: list[tuple[IntentType, ActionType, IntentPolarity]] = []
    elif layout == "quoted-before":
        text = f"{quoted}. Please inspect router.py"
        expected = [
            (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
        ]
    elif layout == "quoted-after":
        text = f"Please inspect router.py. {quoted}"
        expected = [
            (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
        ]
    else:
        text = f"The release report says {payload}; Please inspect router.py"
        expected = [
            (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED)
        ]

    started = time.perf_counter()
    intent = parse_intent(text)
    elapsed = time.perf_counter() - started

    assert _signatures(intent) == expected
    # This is a catastrophic-backtracking guard, not a microbenchmark.  The
    # repaired path is normally well below one second; the previous behavior
    # exceeded ten seconds and sometimes stalled beyond thirty.
    assert elapsed < 3.0


@pytest.mark.parametrize(
    "text",
    [
        "下面不是命令：跑 pytest 并修失败。仅说明意图",
        "下面是一段日志，不是命令：run pytest --maxfail=1",
        (
            "The following is not a command: run pytest and fix failures. "
            "Only explain the intent."
        ),
    ],
)
def test_declared_unquoted_non_commands_never_grant_execution(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type
        in {
            ActionType.CREATE,
            ActionType.UPDATE,
            ActionType.DELETE,
            ActionType.MOVE,
            ActionType.RENAME,
            ActionType.EXECUTE,
        }
        for operation in intent.operations
    )


def test_declared_command_control_still_grants_execution() -> None:
    intent = parse_intent("下面是命令：跑 pytest")

    assert _signatures(intent) == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED)
    ]


def test_declared_non_command_can_be_followed_by_anaphoric_explanation() -> None:
    intent = parse_intent(
        "这不是执行请求：`pytest -q`，只解释它的含义"
    )

    assert _signatures(intent) == [
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "这不是执行请求：`pytest -q`，只解释它的含义",
        "This is not a command: `pytest -q`, only explain what it means.",
    ],
)
def test_read_only_followup_after_non_command_never_reactivates_execution(
    text: str,
) -> None:
    intent = parse_intent(text)

    assert not any(
        operation.action_type is ActionType.EXECUTE
        and operation.polarity is IntentPolarity.AFFIRMED
        for operation in intent.operations
    )


def test_quoted_skill_invocation_is_not_a_task_taxonomy_signal() -> None:
    intent = parse_intent(
        "The docs contain `use security/secret-and-injection-review skill`; "
        "explain this skill routing sentence."
    )

    assert _signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]
    assert "security/secret-and-injection-review" not in intent.keywords


def test_reference_to_current_literal_does_not_inherit_prior_context_action() -> None:
    previous = parse_intent("Review auth.py")
    context = IntentContext.from_messages(
        [{"role": "user", "content": "Review auth.py"}],
        previous_intent=previous,
    )

    intent = parse_intent(
        'The UI displays "continue"; explain that.',
        context=context,
    )

    assert _signatures(intent) == [
        (IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)
    ]
    assert intent.context_used is False
    assert "auth.py" not in intent.entities["files"]


def test_quoted_enum_values_can_be_compared_without_becoming_actions() -> None:
    intent = parse_intent(
        '比较字符串 "refactor.update" 和 "debug.analyze" 的含义，不执行'
    )

    assert _signatures(intent) == [
        (IntentType.QUESTION, ActionType.COMPARE, IntentPolarity.AFFIRMED),
        (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.NEGATED),
    ]


def test_real_implementation_comparison_keeps_review_taxonomy() -> None:
    intent = parse_intent("比较两个 router 实现的差异")

    assert _signatures(intent) == [
        (IntentType.REVIEW, ActionType.COMPARE, IntentPolarity.AFFIRMED)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "海报上准备放三个英文标签：safe-refactor、pytest-debugging、git/commit-hygiene。请仅把这些标签改写成中文短语。",
        "poster labels: lint-and-static-cleanup, dependency-hygiene — rewrite them for non-programmers.",
        "文案素材：testing/regression-from-bug-report、git/commit-hygiene，只是参考名字，不是在调用它们。",
    ],
)
def test_labelled_identifier_enumeration_is_copy_not_task_evidence(text: str) -> None:
    intent = parse_intent(text)

    assert not any(
        keyword
        for keyword in intent.keywords
        if "safe-refactor" in keyword
        or "pytest-debugging" in keyword
        or "commit-hygiene" in keyword
        or "regression-from-bug-report" in keyword
        or "dependency-hygiene" in keyword
    )
    assert not any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.intent_type in {IntentType.REFACTOR, IntentType.DEBUG}
        for operation in intent.operations
    )


def test_label_introducer_does_not_mask_a_real_invocation() -> None:
    intent = parse_intent("使用 refactoring/safe-refactor 技能重构这个函数")

    assert any(
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.intent_type is IntentType.REFACTOR
        for operation in intent.operations
    )
