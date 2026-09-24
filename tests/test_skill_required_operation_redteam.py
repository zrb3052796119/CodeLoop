from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from minicode.capability_registry import build_tool_capability_snapshot
from minicode.intent_parser import parse_intent
from minicode.skill_router import SkillRouter
from minicode.tools import create_default_tool_registry

_REPOSITORY = Path(__file__).resolve().parents[1]
_FIXTURE_DIR = _REPOSITORY / "tests" / "fixtures" / "agent_quality"
_FIXTURE_PATHS = {
    "v1": _FIXTURE_DIR / "skill-required-operation-redteam-v1.json",
    "v2": _FIXTURE_DIR / "skill-required-operation-redteam-v2.json",
}
_FIXTURE_SHA256 = {
    "v1": "e67bbf4914cb66ce72c47aedf06a43c4e7c4dd38226084ca2922911707091a51",
    "v2": "e0563df439f5f43a4fdacc74063ed17ce396b0bc8105432f91d8d90045e0b514",
}
_DOCUMENTS = {
    version: json.loads(path.read_text(encoding="utf-8"))
    for version, path in _FIXTURE_PATHS.items()
}
_SKILL_NAME = "testing/regression-from-bug-report"


def _expanded_v1_cases(document: dict[str, object]) -> list[dict[str, object]]:
    expanded: list[dict[str, object]] = []
    base_required = list(document.get("base_required_operations", []))
    for group in document["groups"]:
        group_required = list(group.get("required_operations", []))
        group_forbidden = list(group.get("forbidden_operations", []))
        for case in group["cases"]:
            expected_veto = bool(group["expected_veto"])
            expanded.append(
                {
                    **case,
                    "version": "v1",
                    "group": group["name"],
                    "prompt": str(document["prompt_template"]).format(
                        skill=_SKILL_NAME,
                        tail=case["tail"],
                    ),
                    "expected_veto": expected_veto,
                    "expected_selected": not expected_veto,
                    "required_operations": [
                        *base_required,
                        *group_required,
                        *case.get("required_operations", []),
                    ],
                    "forbidden_operations": [
                        *group_forbidden,
                        *case.get("forbidden_operations", []),
                    ],
                }
            )
    return expanded


def _expanded_v2_cases(document: dict[str, object]) -> list[dict[str, object]]:
    expanded: list[dict[str, object]] = []
    for case in document["cases"]:
        expected = case["expected"]
        expanded.append(
            {
                **case,
                "version": "v2",
                "group": case["category"],
                "expected_veto": expected["veto"],
                "expected_selected": expected["selected"],
                "required_operations": expected["required_operations"],
                "forbidden_operations": expected["forbidden_operations"],
            }
        )
    return expanded


_CASES = tuple(
    pytest.param(
        case,
        id=f"{case['version']}:{case['id']}:{case['group']}",
    )
    for case in [
        *_expanded_v1_cases(_DOCUMENTS["v1"]),
        *_expanded_v2_cases(_DOCUMENTS["v2"]),
    ]
)


def _operation_matches(operation: object, expected: dict[str, str]) -> bool:
    return (
        operation.intent_type.value == expected["intent"]
        and operation.action_type.value == expected["action"]
        and operation.polarity.value == expected["polarity"]
    )


@pytest.fixture(scope="module")
def production_routing_runtime(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("required-operation-redteam")
    workspace = root / "workspace"
    catalog_target = workspace / ".mini-code" / "skills"
    catalog_target.parent.mkdir(parents=True)
    shutil.copytree(
        _REPOSITORY / ".mini-code" / "skills",
        catalog_target,
        ignore=shutil.ignore_patterns(".DS_Store"),
    )
    with patch.dict(os.environ, {"HOME": str(root / "home")}):
        tools = create_default_tool_registry(
            str(workspace),
            {"toolProfile": "core"},
            include_mcp=False,
        )
    try:
        yield (
            tools.get_skills(),
            build_tool_capability_snapshot(tools),
        )
    finally:
        tools.dispose()


@pytest.mark.parametrize("version", ("v1", "v2"))
def test_required_operation_redteam_fixture_contract(version: str) -> None:
    document = _DOCUMENTS[version]
    fixture_path = _FIXTURE_PATHS[version]
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == (
        _FIXTURE_SHA256[version]
    )
    assert document["schema_version"] == "1.0"

    if version == "v1":
        assert document["skill"] == _SKILL_NAME
        cases = _expanded_v1_cases(document)
        assert len(cases) == 72
        assert len({case["id"] for case in cases}) == 72
        return

    assert document["candidate_sha256"] == {
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
    assert document["source_fixture_for_non_overlap"] == (
        "tests/fixtures/agent_quality/skill-required-operation-redteam-v1.json"
    )
    assert document["exact_tail_overlap_count"] == 0
    assert document["summary"] == {
        "passed": 56,
        "total": 96,
        "accuracy": 0.583333,
        "severity_counts": {
            "P0": 23,
            "PASS": 56,
            "P2": 7,
            "P1": 10,
        },
        "categories": {
            "global_execute": {"P0": 7, "PASS": 9},
            "global_create": {"PASS": 9, "P0": 6, "P2": 1},
            "global_both": {"PASS": 9, "P0": 6, "P2": 1},
            "matching_regression_subtype": {"P0": 4, "PASS": 4},
            "unrelated_scoped_subtype": {
                "P2": 5,
                "PASS": 10,
                "P1": 1,
            },
            "double_negation_controls": {"PASS": 3, "P1": 5},
            "quoted_meta_report_controls": {"PASS": 12, "P1": 4},
        },
    }
    cases = _expanded_v2_cases(document)
    assert len(cases) == 96
    assert len({case["id"] for case in cases}) == 96


@pytest.mark.parametrize("case", _CASES)
def test_required_operation_redteam_case(
    case: dict[str, object],
    production_routing_runtime,
) -> None:
    skill_name = _SKILL_NAME
    prompt = str(case["prompt"])
    intent = parse_intent(prompt)
    skills, capabilities = production_routing_runtime
    routing = SkillRouter().route(
        skills,
        intent,
        capabilities,
        top_k=5,
    )

    errors: list[str] = []
    for required in case["required_operations"]:
        if not any(
            _operation_matches(operation, required)
            for operation in intent.operations
        ):
            errors.append(f"missing required operation {required}")
    for forbidden in case["forbidden_operations"]:
        if any(
            _operation_matches(operation, forbidden)
            for operation in intent.operations
        ):
            errors.append(f"observed forbidden operation {forbidden}")

    requested = skill_name in routing.authority_resolution.requested
    vetoed = skill_name in routing.constraint_vetoed_names
    selected = skill_name in {
        skill.qualified_name for skill in routing.selected
    }
    expected_veto = bool(case["expected_veto"])
    expected_selected = bool(case["expected_selected"])
    if not requested:
        errors.append("explicit production Skill request was not resolved")
    if vetoed is not expected_veto:
        errors.append(f"vetoed={vetoed}, expected {expected_veto}")
    if selected is not expected_selected:
        errors.append(
            f"selected={selected}, expected {expected_selected}"
        )

    signatures = [
        (
            operation.intent_type.value,
            operation.action_type.value,
            operation.polarity.value,
            operation.clause,
        )
        for operation in intent.operations
    ]
    assert not errors, (
        f"version={case['version']} group={case['group']} "
        f"tail={case['tail']!r}\n"
        + "\n".join(errors)
        + f"\noperations={signatures}"
        + f"\nconstraint_vetoed={routing.constraint_vetoed_names}"
        + f"\nselected={[skill.qualified_name for skill in routing.selected]}"
    )
