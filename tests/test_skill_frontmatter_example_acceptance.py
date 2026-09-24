"""Production-path acceptance for every workspace project Skill example."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.capability_registry import build_tool_capability_snapshot
from minicode.skill_router import SkillRouter
from minicode.skill_routing_turn import (
    SkillRoutingTurnAdapters,
    SkillRoutingTurnRequest,
    route_skill_turn,
)
from minicode.skills import SkillSummary, discover_skills
from minicode.tools import create_default_tool_registry

_EXPECTED_SKILL_COUNT = 16
_EXPECTED_EXAMPLE_COUNT = 45
_TOP_K = 5


# This is an executable acceptance contract, not a snapshot generated from the
# parser under test.  A frontmatter example may be edited only together with an
# intentional operation/routing decision here.
_EXPECTED_EXAMPLES: dict[
    tuple[str, str],
    tuple[tuple[str, ...], tuple[tuple[str, tuple[int, ...]], ...]],
] = {
    (
        "code-understanding/codebase-explanation",
        "Explain how agent_loop.py handles tool calls",
    ): (
        ("affirmed:explain.read",),
        (("code-understanding/codebase-explanation", (0,)),),
    ),
    (
        "code-understanding/codebase-explanation",
        "Trace how a user prompt reaches the model",
    ): (
        ("affirmed:explain.read",),
        (("code-understanding/codebase-explanation", (0,)),),
    ),
    (
        "code-understanding/codebase-explanation",
        "Find every caller of route_skill_turn in this codebase",
    ): (
        ("affirmed:search.read",),
        (("code-understanding/codebase-explanation", (0,)),),
    ),
    (
        "code-understanding/design-review",
        "Look at this module and tell me if the design has problems",
    ): (("affirmed:review.analyze",), (("code-understanding/design-review", (0,)),)),
    (
        "code-understanding/design-review",
        "Is there anything inconsistent or unused in this code?",
    ): (("affirmed:review.analyze",), (("code-understanding/design-review", (0,)),)),
    (
        "code-understanding/design-review",
        "Compare the two Skill routing implementations for design tradeoffs",
    ): (("affirmed:review.compare",), (("code-understanding/design-review", (0,)),)),
    (
        "code-understanding/minicode-learning-coach-demo",
        "Teach me how MiniCode agent_loop.py works",
    ): (
        ("affirmed:explain.read",),
        (("code-understanding/minicode-learning-coach-demo", (0,)),),
    ),
    (
        "code-understanding/minicode-learning-coach-demo",
        "Ask me interview questions about MiniCode Skill routing",
    ): (
        ("affirmed:question.create",),
        (("code-understanding/minicode-learning-coach-demo", (0,)),),
    ),
    (
        "documentation/readme-authoring",
        "Update the README for this feature",
    ): (("affirmed:document.update",), (("documentation/readme-authoring", (0,)),)),
    (
        "documentation/readme-authoring",
        "Write a README usage guide for this project",
    ): (("affirmed:document.create",), (("documentation/readme-authoring", (0,)),)),
    (
        "quality/lint-and-static-cleanup",
        "Run ruff check and fix what it finds",
    ): (("affirmed:code.update",), (("quality/lint-and-static-cleanup", (0,)),)),
    (
        "quality/lint-and-static-cleanup",
        "Clean up the lint warnings in this file",
    ): (("affirmed:code.update",), (("quality/lint-and-static-cleanup", (0,)),)),
    (
        "quality/lint-and-static-cleanup",
        "Repair the undefined-name error reported by Ruff",
    ): (("affirmed:debug.update",), (("quality/lint-and-static-cleanup", (0,)),)),
    (
        "code-skills/minicode-study",
        "加载 Skill 后，问：请带我走一遍 main.py 到 agent_loop.py 的启动流程",
    ): (("affirmed:explain.read",), (("code-skills/minicode-study", (0,)),)),
    (
        "code-skills/minicode-study",
        "解释 memory.py 的三层记忆检索管线是如何工作的",
    ): (("affirmed:explain.read",), (("code-skills/minicode-study", (0,)),)),
    (
        "security/secret-and-injection-review",
        "Check this module for security problems",
    ): (
        ("affirmed:review.analyze",),
        (("security/secret-and-injection-review", (0,)),),
    ),
    (
        "security/secret-and-injection-review",
        "Are there any hardcoded secrets or injection risks here?",
    ): (
        ("affirmed:review.analyze",),
        (("security/secret-and-injection-review", (0,)),),
    ),
    (
        "debugging/pytest-debugging",
        "Debug this pytest failure",
    ): (("affirmed:debug.analyze",), (("debugging/pytest-debugging", (0,)),)),
    (
        "debugging/pytest-debugging",
        "Why is tests/test_agent_loop.py failing?",
    ): (("affirmed:debug.analyze",), (("debugging/pytest-debugging", (0,)),)),
    (
        "debugging/pytest-debugging",
        "Repair this broken pytest test",
    ): (("affirmed:debug.update",), (("debugging/pytest-debugging", (0,)),)),
    (
        "debugging/pytest-debugging",
        "Run pytest on the failing assertion",
    ): (("affirmed:test.execute",), (("debugging/pytest-debugging", (0,)),)),
    (
        "debugging/concurrency-debugging",
        "This test fails intermittently, I think it's a race",
    ): (("affirmed:debug.analyze",), (("debugging/concurrency-debugging", (0,)),)),
    (
        "debugging/concurrency-debugging",
        "Find the deadlock in the worker pool",
    ): (("affirmed:debug.analyze",), (("debugging/concurrency-debugging", (0,)),)),
    (
        "dependencies/dependency-hygiene",
        "Add a YAML parser to this project",
    ): (("affirmed:configure.update",), (("dependencies/dependency-hygiene", (0,)),)),
    (
        "dependencies/dependency-hygiene",
        "Upgrade the pinned pytest version and make sure tests still pass",
    ): (
        ("affirmed:configure.update", "affirmed:test.execute"),
        (("dependencies/dependency-hygiene", (0, 1)),),
    ),
    (
        "dependencies/dependency-hygiene",
        "Review the dependency config for stale version pins",
    ): (("affirmed:review.analyze",), (("dependencies/dependency-hygiene", (0,)),)),
    (
        "testing/regression-from-bug-report",
        "Users report deleting the second item removes the wrong one — fix it",
    ): (("affirmed:debug.update",), (("testing/regression-from-bug-report", (0,)),)),
    (
        "testing/regression-from-bug-report",
        "Write a regression test for this bug then fix it",
    ): (
        ("affirmed:test.create", "affirmed:debug.update"),
        (("testing/regression-from-bug-report", (0, 1)),),
    ),
    (
        "testing/regression-from-bug-report",
        "Investigate this user-reported bug",
    ): (("affirmed:debug.analyze",), (("testing/regression-from-bug-report", (0,)),)),
    (
        "testing/regression-from-bug-report",
        "Run the regression test for the reported deletion bug",
    ): (("affirmed:test.execute",), (("testing/regression-from-bug-report", (0,)),)),
    (
        "testing/test-driven-development",
        "Add tests for this new behavior",
    ): (("affirmed:test.create",), (("testing/test-driven-development", (0,)),)),
    (
        "testing/test-driven-development",
        "Implement this with test coverage",
    ): (
        ("affirmed:code.create", "affirmed:test.create"),
        (("testing/test-driven-development", (0, 1)),),
    ),
    (
        "testing/test-driven-development",
        "Run the focused tests for the new behavior",
    ): (("affirmed:test.execute",), (("testing/test-driven-development", (0,)),)),
    (
        "testing/test-driven-development",
        "Implement a new parser feature test-first",
    ): (("affirmed:code.create",), (("testing/test-driven-development", (0,)),)),
    (
        "testing/test-driven-development",
        "Update the implementation after a test-first failure",
    ): (("affirmed:code.update",), (("testing/test-driven-development", (0,)),)),
    (
        "refactoring/safe-refactor",
        "Refactor this module without changing behavior",
    ): (
        ("affirmed:refactor.update", "negated:code.update"),
        (("refactoring/safe-refactor", (0,)),),
    ),
    (
        "refactoring/structural-rename",
        "Rename the taskkit package to todokit everywhere",
    ): (("affirmed:code.rename",), (("refactoring/structural-rename", (0,)),)),
    (
        "refactoring/structural-rename",
        "Move this module and update every import",
    ): (
        ("affirmed:code.rename", "affirmed:code.update"),
        (("refactoring/structural-rename", (0, 1)),),
    ),
    (
        "performance/hotpath-profiling",
        "This command takes 30 seconds, find out why",
    ): (("affirmed:debug.analyze",), (("performance/hotpath-profiling", (0,)),)),
    (
        "performance/hotpath-profiling",
        "Profile the startup path and tell me the bottleneck",
    ): (("affirmed:debug.analyze",), (("performance/hotpath-profiling", (0,)),)),
    (
        "performance/hotpath-profiling",
        "Optimize this module after profiling the startup bottleneck",
    ): (("affirmed:refactor.update",), (("performance/hotpath-profiling", (0,)),)),
    (
        "performance/hotpath-profiling",
        "Run a benchmark to find which parser stage is slow",
    ): (("affirmed:test.execute",), (("performance/hotpath-profiling", (0,)),)),
    (
        "performance/hotpath-profiling",
        "Benchmark parser latency and throughput",
    ): (("affirmed:review.analyze",), (("performance/hotpath-profiling", (0,)),)),
    (
        "git/commit-hygiene",
        "Commit these changes",
    ): (("affirmed:code.update",), (("git/commit-hygiene", (0,)),)),
    (
        "git/commit-hygiene",
        "Review what I'm about to commit and write a message",
    ): (
        ("affirmed:review.analyze", "affirmed:document.create"),
        (("git/commit-hygiene", (0, 1)),),
    ),
}


def _operations(intent: object) -> tuple[str, ...]:
    return tuple(
        (
            f"{operation.polarity.value}:"
            f"{operation.intent_type.value}.{operation.action_type.value}"
        )
        for operation in getattr(intent, "operations", ())
    )


def _selected(routing: object) -> tuple[tuple[str, tuple[int, ...]], ...]:
    return tuple(
        (skill.qualified_name, tuple(skill.operation_indexes))
        for skill in getattr(routing, "selected", ())
    )


def _case_line(
    source_skill: str,
    prompt: str,
    operations: tuple[str, ...],
    selected: tuple[tuple[str, tuple[int, ...]], ...],
) -> str:
    return (
        f"source={source_skill!r} prompt={prompt!r} "
        f"operations={operations!r} selected={selected!r}"
    )


def test_all_project_skill_examples_route_back_to_their_source_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise workspace catalog examples through the production parse/route seam."""
    repository = Path(__file__).resolve().parents[1]
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setattr("minicode.skills._home_dir", lambda: isolated_home)

    # ``compat_project`` may coexist under .claude/skills.  This acceptance
    # contract intentionally covers only the workspace .mini-code catalog.
    skills = [
        skill for skill in discover_skills(repository) if skill.source == "project"
    ]
    examples = [(skill, prompt) for skill in skills for prompt in skill.examples]

    assert len(skills) == _EXPECTED_SKILL_COUNT
    assert len(examples) == _EXPECTED_EXAMPLE_COUNT
    assert all(skill.examples for skill in skills)

    catalog: dict[str, SkillSummary] = {skill.qualified_name: skill for skill in skills}
    runtime_workspace = tmp_path / "runtime-workspace"
    runtime_workspace.mkdir()
    tools = create_default_tool_registry(
        str(runtime_workspace),
        {"toolProfile": "core"},
        include_mcp=False,
    )
    registry = build_tool_capability_snapshot(tools)
    actual_examples = {(skill.qualified_name, prompt) for skill, prompt in examples}
    assert actual_examples == set(_EXPECTED_EXAMPLES)

    mismatches: list[str] = []
    contract_violations: list[str] = []
    lane_violations: list[str] = []
    covered_operations: set[tuple[str, str]] = set()

    try:
        for source_skill, prompt in examples:
            decision = route_skill_turn(
                SkillRoutingTurnRequest(
                    prompt=prompt,
                    skills=skills,
                    registry=registry,
                    top_k=_TOP_K,
                ),
                SkillRoutingTurnAdapters(router=SkillRouter()),
                controls={"skill_selection_mode": "routed"},
            )
            operations = _operations(decision.intent)
            selected = _selected(decision.routing)
            expected_operations, expected_selected = _EXPECTED_EXAMPLES[
                (source_skill.qualified_name, prompt)
            ]
            if operations != expected_operations or selected != expected_selected:
                mismatches.append(
                    _case_line(
                        source_skill.qualified_name,
                        prompt,
                        operations,
                        selected,
                    )
                    + (
                        f" expected_operations={expected_operations!r} "
                        f"expected_selected={expected_selected!r}"
                    )
                )

            affirmed_indexes = tuple(
                index
                for index, operation in enumerate(decision.intent.operations)
                if operation.polarity.value == "affirmed"
            )
            source_routes = [
                routed
                for routed in decision.routing.selected
                if routed.qualified_name == source_skill.qualified_name
            ]
            if (
                len(source_routes) != 1
                or tuple(source_routes[0].operation_indexes) != affirmed_indexes
            ):
                lane_violations.append(
                    _case_line(
                        source_skill.qualified_name,
                        prompt,
                        operations,
                        selected,
                    )
                    + f" affirmed_indexes={affirmed_indexes!r}"
                )

            for routed in decision.routing.selected:
                contract = set(catalog[routed.qualified_name].supported_operations)
                if not contract:
                    continue
                for operation_index in routed.operation_indexes:
                    if not 0 <= operation_index < len(decision.intent.operations):
                        contract_violations.append(
                            _case_line(
                                source_skill.qualified_name,
                                prompt,
                                operations,
                                selected,
                            )
                            + (
                                f" routed={routed.qualified_name!r} "
                                f"invalid_operation_index={operation_index!r}"
                            )
                        )
                        continue
                    operation = decision.intent.operations[operation_index]
                    actual = (
                        f"{operation.intent_type.value}.{operation.action_type.value}"
                    )
                    if actual not in contract:
                        contract_violations.append(
                            _case_line(
                                source_skill.qualified_name,
                                prompt,
                                operations,
                                selected,
                            )
                            + (
                                f" routed={routed.qualified_name!r} "
                                f"claimed={operation_index}:{actual!r} "
                                f"contract={sorted(contract)!r}"
                            )
                        )
                    elif operation.polarity.value == "affirmed":
                        covered_operations.add((routed.qualified_name, actual))
    finally:
        tools.dispose()

    failures: list[str] = []
    if mismatches:
        failures.append(
            f"frozen example mismatches={len(mismatches)}/{len(examples)}:\n"
            + "\n".join(mismatches)
        )
    if contract_violations:
        failures.append(
            f"operation contract violations={len(contract_violations)}:\n"
            + "\n".join(contract_violations)
        )
    if lane_violations:
        failures.append(
            f"source Skill did not cover every affirmed lane="
            f"{len(lane_violations)}:\n" + "\n".join(lane_violations)
        )
    declared_operations = {
        (skill.qualified_name, operation)
        for skill in skills
        for operation in skill.supported_operations
    }
    uncovered = sorted(declared_operations - covered_operations)
    if uncovered:
        failures.append(f"uncovered supported_operations={uncovered!r}")

    assert not failures, "\n\n".join(failures)
