"""Regression tests for the regex-based intent classifier.

These lock in a specific, previously-unguarded failure class: a bare
explain/configure verb ("what is", "tell", "install", "init"...) with no
required following context matched nearly any everyday sentence with full
confidence, misrouting unrelated small talk into EXPLAIN/CONFIGURE instead
of abstaining as UNKNOWN.
"""
from __future__ import annotations

import pytest

from minicode.intent_parser import ActionType, IntentType, parse_intent


@pytest.mark.parametrize(
    "text",
    [
        "explain how agent_loop.py handles tool calls",
        "explain agent_loop.py",
        "what is this function doing in memory.py",
        "how does skill routing work",
        "describe the tool calling flow",
        "解释 agent_loop.py 为什么会调用工具",
        "解释一下这段代码",
        "说明一下这个架构的设计",
    ],
)
def test_explain_pattern_still_matches_real_code_questions(text: str) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == IntentType.EXPLAIN
    assert intent.action_type == ActionType.READ
    assert intent.confidence > 0.0


@pytest.mark.parametrize(
    "text",
    [
        "What is the weather like today",
        "Tell me a joke",
        "How to bake a cake",
        "Describe your day",
        "tell me about your weekend",
        "说明一下为什么今天天气这么热",
        "讲讲你今天心情怎么样",
    ],
)
def test_explain_pattern_does_not_match_unrelated_small_talk(text: str) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == IntentType.UNKNOWN
    assert intent.action_type == ActionType.UNKNOWN
    assert intent.confidence == 0.0


@pytest.mark.parametrize(
    "text",
    [
        "configure the model settings",
        "set up the environment for this project",
        "install the dependencies",
        "init the repo config",
    ],
)
def test_configure_pattern_still_matches_real_configuration_requests(text: str) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == IntentType.CONFIGURE
    assert intent.action_type == ActionType.UPDATE
    assert intent.confidence > 0.0


@pytest.mark.parametrize(
    "text",
    [
        "Set up a meeting for tomorrow",
        "Install a new habit",
        "She said she felt infinite joy",
    ],
)
def test_configure_pattern_does_not_match_unrelated_everyday_requests(text: str) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == IntentType.UNKNOWN
    assert intent.action_type == ActionType.UNKNOWN


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        # Every one of these previously failed to match at all: the old
        # patterns required the trigger verb to be followed by nothing but
        # whitespace before the target noun, so a determiner, preposition,
        # short adjective, or plural noun broke the match entirely — even
        # though these are the normal, idiomatic way to phrase the request.
        ("write a new function to parse CSV files", IntentType.CODE, ActionType.CREATE),
        ("modify the code in this file to add logging", IntentType.CODE, ActionType.UPDATE),
        ("debug this error in the login flow", IntentType.DEBUG, ActionType.ANALYZE),
        ("refactor this code to simplify the logic", IntentType.REFACTOR, ActionType.UPDATE),
        ("search for the function that handles login", IntentType.SEARCH, ActionType.READ),
        ("run the tests and verify they pass", IntentType.TEST, ActionType.EXECUTE),
        ("document this function with a docstring", IntentType.DOCUMENT, ActionType.CREATE),
    ],
)
def test_patterns_tolerate_determiners_prepositions_and_plurals(
    text: str, expected_intent: IntentType, expected_action: ActionType
) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == expected_intent
    assert intent.action_type == expected_action
    assert intent.confidence > 0.0


@pytest.mark.parametrize(
    "text",
    [
        "I want to write a novel about space travel",
        "please fix your posture",
        "clean the kitchen counter",
        "find my keys somewhere in the house",
    ],
)
def test_determiner_tolerance_does_not_reopen_false_positives(text: str) -> None:
    """The bounded gap added for determiner/preposition tolerance must stay
    narrow enough that it doesn't turn back into the bare-verb-matches-
    anything bug the EXPLAIN/CONFIGURE fixes closed."""
    intent = parse_intent(text)
    assert intent.intent_type == IntentType.UNKNOWN


@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_action"),
    [
        (
            "the Python test traceback fails only under pytest, investigate it",
            IntentType.DEBUG,
            ActionType.ANALYZE,
        ),
        (
            "trace which classes and modules call this function",
            IntentType.EXPLAIN,
            ActionType.READ,
        ),
        (
            "write docstrings and a migration guide for this module",
            IntentType.DOCUMENT,
            ActionType.CREATE,
        ),
        (
            "design a responsive frontend landing page and component layout",
            IntentType.CODE,
            ActionType.CREATE,
        ),
        (
            "check context compaction summary fidelity under token pressure",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
        (
            "design multi-agent task delegation with parallel sub-agents",
            IntentType.CODE,
            ActionType.CREATE,
        ),
        (
            "create a Git branch and conventional commit for these changes",
            IntentType.CONFIGURE,
            ActionType.UPDATE,
        ),
        (
            "profile the CPU and latency performance regression",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
        (
            "检查上下文压缩后是否丢失摘要中的关键决定",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
        (
            "设计多智能体并行协作和子代理任务委派",
            IntentType.CODE,
            ActionType.CREATE,
        ),
        (
            "审计登录鉴权、权限和密钥泄漏风险",
            IntentType.REVIEW,
            ActionType.ANALYZE,
        ),
    ],
)
def test_bounded_engineering_phrases_classify_without_bare_verb_leakage(
    text: str,
    expected_intent: IntentType,
    expected_action: ActionType,
) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == expected_intent
    assert intent.action_type == expected_action


@pytest.mark.parametrize(
    "text",
    [
        "I bought a memory foam pillow",
        "This camera needs a larger memory card",
        "The deployment of troops ended at dawn",
        "Which travel agent should I call?",
    ],
)
def test_engineering_nouns_in_everyday_prose_still_abstain(text: str) -> None:
    intent = parse_intent(text)
    assert intent.intent_type == IntentType.UNKNOWN
    assert intent.action_type == ActionType.UNKNOWN
