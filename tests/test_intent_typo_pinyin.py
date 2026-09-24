"""Conservative regressions for typo, pinyin, and mixed-language requests."""

from __future__ import annotations

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentPolarity,
    IntentType,
    parse_intent,
)

Signature = tuple[IntentType, ActionType, IntentPolarity]


def _signatures(text: str) -> list[Signature]:
    return [
        (operation.intent_type, operation.action_type, operation.polarity)
        for operation in parse_intent(text).operations
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "pls expln agent_loop咋把tool call塞回去的",
            [(IntentType.EXPLAIN, ActionType.READ, IntentPolarity.AFFIRMED)],
        ),
        (
            "rn pytes，挂的那条顺手修咯",
            [
                (IntentType.TEST, ActionType.EXECUTE, IntentPolarity.AFFIRMED),
                (IntentType.DEBUG, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "refacor这个parser，行为保持哈",
            [(IntentType.REFACTOR, ActionType.UPDATE, IntentPolarity.AFFIRMED)],
        ),
        (
            "cha一下woker pool为啥deallock",
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
        (
            "ba memory pkg gai名，import都bie漏",
            [
                (IntentType.CODE, ActionType.RENAME, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "README yongfa bu一下",
            [(IntentType.DOCUMENT, ActionType.UPDATE, IntentPolarity.AFFIRMED)],
        ),
        (
            "reqs里的pytes pin sheng到新版本",
            [(IntentType.CONFIGURE, ActionType.UPDATE, IntentPolarity.AFFIRMED)],
        ),
        (
            "profil一下startup，看看慢zai哪",
            [(IntentType.DEBUG, ActionType.ANALYZE, IntentPolarity.AFFIRMED)],
        ),
        (
            "按TDD整个parser feat",
            [
                (IntentType.TEST, ActionType.CREATE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.CREATE, IntentPolarity.AFFIRMED),
            ],
        ),
        (
            "git diff瞅眼 然后comit",
            [
                (IntentType.REVIEW, ActionType.ANALYZE, IntentPolarity.AFFIRMED),
                (IntentType.CODE, ActionType.UPDATE, IntentPolarity.AFFIRMED),
            ],
        ),
    ],
)
def test_bounded_noisy_forms_recover_explicit_technical_actions(
    text: str,
    expected: list[Signature],
) -> None:
    assert _signatures(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "expln 是什么缩写",
        "refacor 这个词是什么意思",
        "cha 是这里的变量名",
        "pin 号忘了怎么办",
        "comit 只是日志里的普通字符串",
        "README yongfa",
        "profil 是用户填写的昵称",
        "去 gai名山 景区看看",
    ],
)
def test_noisy_fragments_without_a_technical_action_do_not_grant_authority(
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
