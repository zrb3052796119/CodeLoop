"""Independent messy-language regression matrix for intent authority.

The fixture was produced by a read-only red-team pass against a locked
candidate.  It intentionally mixes English, Chinese, context switches,
reported text, explanation-only requests, and compound prohibitions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from minicode.intent_parser import (
    ActionType,
    IntentContext,
    IntentOperation,
    IntentPolarity,
    IntentType,
    parse_intent,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "agent_quality"
_FIXTURE_PATHS = {
    "v1": _FIXTURE_DIR / "intent-language-redteam-v1.json",
    "v2": _FIXTURE_DIR / "intent-language-redteam-v2.json",
    "v3": _FIXTURE_DIR / "intent-language-redteam-v3.json",
}
_FIXTURES = {
    version: json.loads(path.read_text(encoding="utf-8"))
    for version, path in _FIXTURE_PATHS.items()
}
_CASES = tuple(
    pytest.param(case, id=f"{version}:{case['id']}:{case['cluster']}")
    for version, fixture in _FIXTURES.items()
    for case in fixture["cases"]
)
_READ_ONLY_ACTIONS = {
    ActionType.READ,
    ActionType.ANALYZE,
    ActionType.COMPARE,
    ActionType.UNKNOWN,
}
_EFFECTFUL_ACTIONS = frozenset(ActionType) - _READ_ONLY_ACTIONS
_NO_EFFECT_CLUSTERS = {
    "status",
    "launder",
    "explain",
    "view",
    "objectless",
}
_TARGET_UPDATE_CLUSTERS = {"outer", "direct", "denial", "compound"}
_ATTRIBUTED_TARGET_MARKERS = {"attributed_target", "any_attributed_target"}


def _context(payload: dict[str, Any] | None) -> IntentContext | None:
    if payload is None:
        return None
    return IntentContext.from_messages(
        payload.get("messages", ()),
        active_task=payload.get("active_task"),
    )


def _describe(operation: IntentOperation) -> dict[str, Any]:
    return {
        "intent": operation.intent_type.value,
        "action": operation.action_type.value,
        "polarity": operation.polarity.value,
        "files": list(operation.entities.get("files", ())),
        "clause": operation.clause,
    }


def _has(
    operations: list[IntentOperation],
    intent: str,
    action: str,
    polarity: str = "affirmed",
) -> bool:
    return any(
        operation.intent_type.value == intent
        and operation.action_type.value == action
        and operation.polarity.value == polarity
        for operation in operations
    )


def _is_effectful(operation: IntentOperation) -> bool:
    return (
        operation.polarity is IntentPolarity.AFFIRMED
        and operation.action_type in _EFFECTFUL_ACTIONS
    )


def _matches_choice(actual: str, expected: str | list[str] | None) -> bool:
    if expected is None:
        return True
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def _matches_expected_operation(
    operation: IntentOperation,
    expected: dict[str, Any],
) -> bool:
    return bool(
        _matches_choice(operation.intent_type.value, expected.get("intent"))
        and _matches_choice(operation.action_type.value, expected.get("action"))
        and _matches_choice(operation.polarity.value, expected.get("polarity"))
        and set(operation.entities.get("files", ()))
        == set(expected.get("entities", {}).get("files", ()))
    )


def _affirmed_files(operations: list[IntentOperation]) -> list[str]:
    return [
        file
        for operation in operations
        if operation.polarity.value == "affirmed"
        for file in operation.entities.get("files", ())
    ]


def _has_update_file(
    operations: list[IntentOperation],
    file: str,
) -> bool:
    return any(
        operation.polarity.value == "affirmed"
        and operation.intent_type.value in {"code", "debug"}
        and operation.action_type.value == "update"
        and file in operation.entities.get("files", ())
        for operation in operations
    )


def _expected_update_file(case: dict[str, Any]) -> str:
    return next(
        file
        for operation in case["expected"]["operations"]
        if operation["action"] == "update"
        for file in operation["entities"]["files"]
    )


def _effectful_operation_is_allowed(
    case: dict[str, Any],
    operation: IntentOperation,
) -> bool:
    cluster = case["cluster"]
    files = set(operation.entities.get("files", ()))
    if cluster in _NO_EFFECT_CLUSTERS:
        return False
    if cluster == "command":
        return (
            operation.intent_type is IntentType.TEST
            and operation.action_type is ActionType.EXECUTE
            and not files
        )
    if cluster in _TARGET_UPDATE_CLUSTERS:
        return (
            operation.intent_type in {IntentType.CODE, IntentType.DEBUG}
            and operation.action_type is ActionType.UPDATE
            and files == {_expected_update_file(case)}
        )
    raise AssertionError(f"unsupported cluster: {cluster}")


def _effectful_operations_are_allowed(
    case: dict[str, Any],
    operations: list[IntentOperation],
) -> bool:
    return all(
        _effectful_operation_is_allowed(case, operation)
        for operation in operations
        if _is_effectful(operation)
    )


def _matches_forbidden_operation(
    case: dict[str, Any],
    operation: IntentOperation,
    forbidden: dict[str, Any],
) -> bool:
    expected_polarity = forbidden.get("polarity")
    if not _matches_choice(operation.polarity.value, expected_polarity):
        return False
    expected_intent = forbidden.get("intent")
    if not _matches_choice(operation.intent_type.value, expected_intent):
        return False
    expected_action = forbidden.get("action")
    if not _matches_choice(operation.action_type.value, expected_action):
        return False
    if forbidden.get("effectful") and not _is_effectful(operation):
        return False

    forbidden_files = set(forbidden.get("entities", {}).get("files", ()))
    operation_files = set(operation.entities.get("files", ()))
    if forbidden_files & _ATTRIBUTED_TARGET_MARKERS:
        return _is_effectful(operation) and operation_files != {
            _expected_update_file(case)
        }
    if forbidden_files:
        return bool(forbidden_files & operation_files)
    if expected_intent is None and expected_action is None:
        return _is_effectful(operation)
    return True


def _violates_explicit_forbidden(
    case: dict[str, Any],
    operations: list[IntentOperation],
) -> bool:
    return any(
        _matches_forbidden_operation(case, operation, forbidden)
        for forbidden in case["expected"].get("forbidden", ())
        for operation in operations
    )


def _passes_authority_oracle(
    case: dict[str, Any],
    operations: list[IntentOperation],
) -> bool:
    if case["expected"].get("semantics") == "strict_required_plus_forbidden":
        expected_operations = case["expected"]["operations"]
        if case["expected"].get("exact_empty") and operations:
            return False
        if _violates_explicit_forbidden(case, operations):
            return False
        if any(
            not any(
                _matches_expected_operation(operation, expected)
                for operation in operations
            )
            for expected in expected_operations
        ):
            return False
        return all(
            any(
                _matches_expected_operation(operation, expected)
                for expected in expected_operations
                if expected.get("polarity") == "affirmed"
                and any(
                    action in {item.value for item in _EFFECTFUL_ACTIONS}
                    for action in (
                        expected.get("action")
                        if isinstance(expected.get("action"), list)
                        else [expected.get("action")]
                    )
                )
            )
            for operation in operations
            if _is_effectful(operation)
        )

    cluster = case["cluster"]
    if _violates_explicit_forbidden(case, operations):
        return False
    if not _effectful_operations_are_allowed(case, operations):
        return False
    if cluster in {"status", "launder"}:
        return not operations
    if cluster == "command":
        return _has(operations, "test", "execute")
    if cluster == "outer":
        return _has_update_file(operations, _expected_update_file(case))
    if cluster == "explain":
        return _has(operations, "explain", "read")
    if cluster == "direct":
        return _has_update_file(operations, _expected_update_file(case))
    if cluster == "view":
        target = next(
            file
            for operation in case["expected"]["operations"]
            for file in operation["entities"]["files"]
        )
        files = _affirmed_files(operations)
        return target in files and "old.py" not in files
    if cluster == "objectless":
        return any(
            operation.polarity.value == "affirmed"
            and operation.action_type.value in {"read", "analyze"}
            for operation in operations
        )
    if cluster == "denial":
        denied_action = next(
            operation["action"]
            for operation in case["expected"]["operations"]
            if operation["intent"] == "test"
            and operation["polarity"] == "negated"
        )
        return bool(
            _has(operations, "test", denied_action, "negated")
            and not _has(operations, "test", denied_action)
            and _has_update_file(
                operations,
                _expected_update_file(case),
            )
        )
    if cluster == "compound":
        return bool(
            _has(operations, "test", "create", "negated")
            and _has(operations, "test", "execute", "negated")
            and not _has(operations, "test", "create")
            and not _has(operations, "test", "execute")
            and _has_update_file(
                operations,
                _expected_update_file(case),
            )
        )
    raise AssertionError(f"unsupported cluster: {cluster}")


def _synthetic_operation(
    intent: IntentType,
    action: ActionType,
    *,
    polarity: IntentPolarity = IntentPolarity.AFFIRMED,
    files: tuple[str, ...] = (),
) -> IntentOperation:
    return IntentOperation(
        intent_type=intent,
        action_type=action,
        polarity=polarity,
        confidence=1.0,
        clause="oracle mutation probe",
        entities={"files": files},
    )


def _minimal_passing_operations(case: dict[str, Any]) -> list[IntentOperation]:
    cluster = case["cluster"]
    if cluster == "double_negation":
        expected = case["expected"]["operations"][0]
        expected_intent = expected["intent"]
        expected_action = expected["action"]
        return [
            _synthetic_operation(
                IntentType(
                    expected_intent[0]
                    if isinstance(expected_intent, list)
                    else expected_intent
                ),
                ActionType(
                    expected_action[0]
                    if isinstance(expected_action, list)
                    else expected_action
                ),
                polarity=IntentPolarity(expected["polarity"]),
                files=tuple(expected["entities"]["files"]),
            )
        ]
    if cluster in {"status", "launder"}:
        return []
    if cluster == "command":
        return [_synthetic_operation(IntentType.TEST, ActionType.EXECUTE)]
    if cluster in _TARGET_UPDATE_CLUSTERS:
        operations = [
            _synthetic_operation(
                IntentType.CODE,
                ActionType.UPDATE,
                files=(_expected_update_file(case),),
            )
        ]
        if cluster == "denial":
            denied_action = next(
                operation["action"]
                for operation in case["expected"]["operations"]
                if operation["intent"] == "test"
                and operation["polarity"] == "negated"
            )
            operations.insert(
                0,
                _synthetic_operation(
                    IntentType.TEST,
                    ActionType(denied_action),
                    polarity=IntentPolarity.NEGATED,
                ),
            )
        elif cluster == "compound":
            operations[:0] = [
                _synthetic_operation(
                    IntentType.TEST,
                    ActionType.CREATE,
                    polarity=IntentPolarity.NEGATED,
                ),
                _synthetic_operation(
                    IntentType.TEST,
                    ActionType.EXECUTE,
                    polarity=IntentPolarity.NEGATED,
                ),
            ]
        return operations
    if cluster == "explain":
        files = tuple(
            file
            for operation in case["expected"]["operations"]
            for file in operation["entities"]["files"]
        )
        return [
            _synthetic_operation(IntentType.EXPLAIN, ActionType.READ, files=files)
        ]
    if cluster == "view":
        target = next(
            file
            for operation in case["expected"]["operations"]
            for file in operation["entities"]["files"]
        )
        return [
            _synthetic_operation(
                IntentType.REVIEW,
                ActionType.ANALYZE,
                files=(target,),
            )
        ]
    if cluster == "objectless":
        return [_synthetic_operation(IntentType.REVIEW, ActionType.ANALYZE)]
    raise AssertionError(f"unsupported cluster: {cluster}")


_REPRESENTATIVE_CASES = {
    cluster: next(
        case
        for case in _FIXTURES["v1"]["cases"]
        if case["cluster"] == cluster
    )
    for cluster in (
        "status",
        "command",
        "launder",
        "outer",
        "explain",
        "direct",
        "view",
        "objectless",
        "denial",
        "compound",
    )
}
_REPRESENTATIVE_CASES["double_negation"] = next(
    case
    for case in _FIXTURES["v3"]["cases"]
    if case["cluster"] == "double_negation"
    and case["expected"]["operations"][0]["action"] == "update"
)
_INJECTED_EFFECTS = (
    (IntentType.CODE, ActionType.DELETE, ("unexpected.py",)),
    (IntentType.MEMORY, ActionType.DELETE, ()),
    (IntentType.TEST, ActionType.EXECUTE, ()),
    (IntentType.CODE, ActionType.UPDATE, ("unexpected.py",)),
)
_MUTATION_INJECTION_CASES = tuple(
    pytest.param(
        case,
        intent,
        action,
        files,
        id=f"{cluster}:{intent.value}.{action.value}",
    )
    for cluster, case in _REPRESENTATIVE_CASES.items()
    for intent, action, files in _INJECTED_EFFECTS
    if not (
        cluster == "command"
        and intent is IntentType.TEST
        and action is ActionType.EXECUTE
    )
)


@pytest.mark.parametrize(
    "case",
    _CASES,
)
def test_independent_language_redteam_oracle(case: dict[str, Any]) -> None:
    operations = list(
        parse_intent(
            case["prompt"],
            context=_context(case.get("context")),
        ).operations
    )
    actual = [_describe(operation) for operation in operations]
    assert _passes_authority_oracle(case, operations), actual


@pytest.mark.parametrize(
    ("case", "intent", "action", "files"),
    _MUTATION_INJECTION_CASES,
)
def test_authority_oracle_rejects_injected_effects(
    case: dict[str, Any],
    intent: IntentType,
    action: ActionType,
    files: tuple[str, ...],
) -> None:
    baseline = _minimal_passing_operations(case)
    assert _passes_authority_oracle(case, baseline)

    injected = _synthetic_operation(intent, action, files=files)
    assert not _passes_authority_oracle(case, [*baseline, injected])


@pytest.mark.parametrize("version", ("v1", "v2", "v3"))
def test_language_redteam_fixture_identity(version: str) -> None:
    fixture = _FIXTURES[version]
    fixture_path = _FIXTURE_PATHS[version]
    assert fixture["match_policy"] == (
        "strict-required-forbidden-v1"
        if version == "v3"
        else "cluster-authority-v1"
    )

    if version == "v1":
        assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == (
            "2422c6be5ae44942f61f1353ed66b3e948b69799ff526f1f9ffbf960466973cb"
        )
        assert fixture["schema_version"] == "intent-language-redteam-v1"
        assert fixture["source_sha256"] == (
            "98dbb9c214c57658653e77dbdc14a6520987fb4ff9a11d850fdaf5d6047ba7a2"
        )
        assert fixture["baseline"] == {
            "passed": 60,
            "total": 130,
            "p0": 32,
            "p1": 36,
            "p2": 2,
        }
        assert len(fixture["cases"]) == 130
        return

    if version == "v2":
        assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == (
            "4784e54bf1b0e3ec67584134a18943d870f51e2b0db0096f9d66a575a2d56945"
        )
        assert fixture["schema_version"] == "intent-language-redteam-fresh120-v1"
        assert fixture["source_exclusion"] == {
            "fixture": "tests/fixtures/agent_quality/intent-language-redteam-v1.json",
            "exact_prompt_overlap": 0,
        }
        assert fixture["candidate_sha256"] == {
            "minicode/intent_parser.py": (
                "2ceacbe1e3303792a5b7c4133c3332f6928b42f1ee51dec389b853123b92c4ca"
            ),
            "minicode/intent_negation.py": (
                "38cc687427a0769d63d154d355a4ea8557290ec1068c23b69316c2775032ca9f"
            ),
            "minicode/skill_router.py": (
                "813447801f2d2b7c64903f56429b05a582241ea553c33f06d1462141263f1b08"
            ),
        }
        assert fixture["summary"] == {
            "passed": 69,
            "total": 120,
            "accuracy": 0.575,
            "p0": 26,
            "p1": 25,
            "p2": 0,
            "clusters": {
                "status": {"passed": 7, "total": 12},
                "command": {"passed": 11, "total": 12},
                "launder": {"passed": 3, "total": 12},
                "outer": {"passed": 3, "total": 12},
                "explain": {"passed": 7, "total": 12},
                "direct": {"passed": 11, "total": 12},
                "view": {"passed": 9, "total": 12},
                "objectless": {"passed": 1, "total": 12},
                "denial": {"passed": 8, "total": 12},
                "compound": {"passed": 9, "total": 12},
            },
        }
        assert len(fixture["cases"]) == 120
        return

    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == (
        "c48f2ba9a9a1edcb17c264903dee525d23908f4af1432adf5e76094996d526a5"
    )
    assert fixture["schema_version"] == "intent-language-redteam-blind-v3"
    assert fixture["source_exclusion"] == {
        "fixtures": [
            "tests/fixtures/agent_quality/intent-language-redteam-v1.json",
            "tests/fixtures/agent_quality/intent-language-redteam-v2.json",
        ],
        "exact_prompt_overlap": 0,
    }
    assert fixture["candidate_sha256"] == {
        "minicode/intent_parser.py": (
            "bc526b1396ba77f77c944f3d3d031e11642fb4496ae759069993d3a5fd460651"
        ),
        "minicode/intent_negation.py": (
            "cab19b8be9c220a23d21dd971fb09e981f4bcdc5f7484abfcc2b9625f72c85f9"
        ),
        "minicode/skill_router.py": (
            "8d6dc274ea52632c20e597e127fec739bb768e14067ae32ef3bc437c92e404eb"
        ),
    }
    assert fixture["summary"] == {
        "passed": 55,
        "failed": 77,
        "total": 132,
        "accuracy": 0.416667,
        "p0": 32,
        "p1": 43,
        "p2": 2,
        "clusters": {
            "status": {
                "passed": 6,
                "failed": 6,
                "total": 12,
                "accuracy": 0.5,
                "p0": 5,
                "p1": 0,
                "p2": 1,
            },
            "command": {
                "passed": 7,
                "failed": 5,
                "total": 12,
                "accuracy": 0.583333,
                "p0": 0,
                "p1": 5,
                "p2": 0,
            },
            "launder": {
                "passed": 3,
                "failed": 9,
                "total": 12,
                "accuracy": 0.25,
                "p0": 9,
                "p1": 0,
                "p2": 0,
            },
            "outer": {
                "passed": 3,
                "failed": 9,
                "total": 12,
                "accuracy": 0.25,
                "p0": 8,
                "p1": 1,
                "p2": 0,
            },
            "explain": {
                "passed": 7,
                "failed": 5,
                "total": 12,
                "accuracy": 0.583333,
                "p0": 4,
                "p1": 0,
                "p2": 1,
            },
            "direct": {
                "passed": 9,
                "failed": 3,
                "total": 12,
                "accuracy": 0.75,
                "p0": 2,
                "p1": 1,
                "p2": 0,
            },
            "view": {
                "passed": 8,
                "failed": 4,
                "total": 12,
                "accuracy": 0.666667,
                "p0": 0,
                "p1": 4,
                "p2": 0,
            },
            "objectless": {
                "passed": 2,
                "failed": 10,
                "total": 12,
                "accuracy": 0.166667,
                "p0": 0,
                "p1": 10,
                "p2": 0,
            },
            "denial": {
                "passed": 8,
                "failed": 4,
                "total": 12,
                "accuracy": 0.666667,
                "p0": 0,
                "p1": 4,
                "p2": 0,
            },
            "compound": {
                "passed": 2,
                "failed": 10,
                "total": 12,
                "accuracy": 0.166667,
                "p0": 2,
                "p1": 8,
                "p2": 0,
            },
            "double_negation": {
                "passed": 0,
                "failed": 12,
                "total": 12,
                "accuracy": 0.0,
                "p0": 2,
                "p1": 10,
                "p2": 0,
            },
        },
    }
    assert len(fixture["cases"]) == 132
    assert sum(case["passed"] for case in fixture["cases"]) == 55
    assert all(
        {"actual", "passed", "severity", "failure_reasons"} <= case.keys()
        for case in fixture["cases"]
    )
