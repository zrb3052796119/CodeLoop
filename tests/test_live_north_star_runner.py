from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.build_memory_compaction_north_star_manifest import (
    SUITE_ID as MEMORY_COMPACTION_SUITE_ID,
    build_addendum_manifest,
    build_manifest as build_memory_compaction_manifest,
)
from scripts.build_north_star_live_manifest import SUITE_ID, build_cases
from scripts.run_north_star_live import (
    TurnEvidence,
    _evaluate_oracle,
    _isolated_write_approval,
    _run_command_oracle,
    _safe_relative,
    _tree_digest,
    _validate_manifest,
)


def _turn_with_events(*events: tuple[str, dict]) -> TurnEvidence:
    projected = tuple(
        SimpleNamespace(type=event_type, payload=payload)
        for event_type, payload in events
    )
    return TurnEvidence(
        run_id="run_" + "a" * 32,
        response="",
        event_types=tuple(event.type for event in projected),
        events=projected,
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
    )


def test_memory_injected_oracle_rejects_an_empty_render_event(tmp_path: Path) -> None:
    empty = _turn_with_events(
        ("memory.rendered", {"injected": False, "renderedCount": 0})
    )
    injected = _turn_with_events(
        ("memory.rendered", {"injected": True, "renderedCount": 1})
    )
    oracle = {"kind": "memory_injected", "min": 1}

    assert not _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[empty],
        before_digest="",
        journal=object(),
    )
    assert _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[injected],
        before_digest="",
        journal=object(),
    )


def test_context_compaction_count_oracle_requires_the_declared_minimum(
    tmp_path: Path,
) -> None:
    one = _turn_with_events(
        ("context.compacted", {"effective": True}),
    )
    two = _turn_with_events(
        ("context.compacted", {"effective": True}),
        ("context.compacted", {"effective": True}),
    )
    oracle = {"kind": "context_compaction_count", "min": 2}

    assert not _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[one],
        before_digest="",
        journal=object(),
    )
    assert _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[two],
        before_digest="",
        journal=object(),
    )


def test_tool_succeeded_oracle_requires_a_paired_success_in_the_same_turn(
    tmp_path: Path,
) -> None:
    started_only = _turn_with_events(
        (
            "tool.started",
            {"toolName": "read_file", "operationId": "toolop_one"},
        )
    )
    failed = _turn_with_events(
        (
            "tool.started",
            {"toolName": "read_file", "operationId": "toolop_two"},
        ),
        (
            "tool.finished",
            {
                "toolName": "read_file",
                "operationId": "toolop_two",
                "outcome": "error",
                "paired": True,
            },
        ),
    )
    orphaned = _turn_with_events(
        (
            "tool.finished",
            {
                "toolName": "read_file",
                "operationId": "toolop_three",
                "outcome": "success",
                "paired": True,
            },
        )
    )
    succeeded = _turn_with_events(
        (
            "tool.started",
            {"toolName": "read_file", "operationId": "toolop_four"},
        ),
        (
            "tool.finished",
            {
                "toolName": "read_file",
                "operationId": "toolop_four",
                "outcome": "success",
                "paired": True,
            },
        ),
    )
    oracle = {"kind": "tool_succeeded", "toolName": "read_file"}

    for turn in (started_only, failed, orphaned):
        assert not _evaluate_oracle(
            oracle,
            workspace=tmp_path,
            turns=[turn],
            before_digest="",
            journal=object(),
        )
    assert not _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[started_only, orphaned],
        before_digest="",
        journal=object(),
    )
    assert _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[succeeded],
        before_digest="",
        journal=object(),
    )
    assert _evaluate_oracle(
        {"kind": "tool_failed", "toolName": "read_file"},
        workspace=tmp_path,
        turns=[failed],
        before_digest="",
        journal=object(),
    )
    assert not _evaluate_oracle(
        {"kind": "tool_failed", "toolName": "read_file"},
        workspace=tmp_path,
        turns=[succeeded],
        before_digest="",
        journal=object(),
    )


def test_tool_succeeded_oracle_counts_unique_completed_operations(
    tmp_path: Path,
) -> None:
    first = {
        "toolName": "read_file",
        "operationId": "toolop_one",
        "outcome": "success",
        "paired": True,
    }
    evidence = _turn_with_events(
        (
            "tool.started",
            {"toolName": "read_file", "operationId": "toolop_one"},
        ),
        ("tool.finished", first),
        ("tool.finished", first),
        (
            "tool.started",
            {"toolName": "read_file", "operationId": "toolop_two"},
        ),
        (
            "tool.finished",
            {
                "toolName": "read_file",
                "operationId": "toolop_two",
                "outcome": "success",
                "paired": True,
            },
        ),
    )

    assert _evaluate_oracle(
        {"kind": "tool_succeeded", "toolName": "read_file", "min": 2},
        workspace=tmp_path,
        turns=[evidence],
        before_digest="",
        journal=object(),
    )
    assert not _evaluate_oracle(
        {"kind": "tool_succeeded", "toolName": "read_file", "min": 3},
        workspace=tmp_path,
        turns=[evidence],
        before_digest="",
        journal=object(),
    )


@pytest.mark.parametrize(
    ("tool_name", "minimum"),
    [("../read_file", 1), ("read_file", 0), ("read_file", True)],
)
def test_live_manifest_rejects_invalid_tool_success_oracles(
    tool_name: object,
    minimum: object,
) -> None:
    case = dict(build_cases()[0])
    case["oracles"] = [
        {
            "id": "source-read",
            "kind": "tool_succeeded",
            "toolName": tool_name,
            "min": minimum,
        }
    ]
    case["oracleIds"] = ["source-read"]

    with pytest.raises(ValueError, match="tool_succeeded"):
        _validate_manifest(
            {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [case]}
        )


def test_live_manifest_meets_declared_a_north_star_shape() -> None:
    cases = build_cases()
    categories = Counter(case["category"] for case in cases)

    assert len(cases) == 50
    assert len(categories) >= 8
    assert sum(case["mutability"] == "write" for case in cases) >= 20
    assert len({case["id"] for case in cases}) == 50
    assert all(case["turns"] and case["oracles"] for case in cases)

    validated = _validate_manifest(
        {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": cases}
    )
    assert [case["id"] for case in validated] == [
        case["id"] for case in cases
    ]


def test_memory_compaction_manifest_freezes_twenty_tasks_and_strict_oracles() -> None:
    document = build_memory_compaction_manifest()
    cases = _validate_manifest(document)

    assert document["suiteId"] == MEMORY_COMPACTION_SUITE_ID
    assert len(cases) == 17
    assert sum(len(case["turns"]) for case in cases) == 20
    assert Counter(case["category"].split("-")[0] for case in cases) == {
        "persistent": 8,
        "context": 9,
    }
    assert sum(
        oracle["kind"] == "memory_injected"
        for case in cases
        for oracle in case["oracles"]
    ) == 4
    assert sum(
        oracle["kind"] == "context_compaction_count"
        for case in cases
        for oracle in case["oracles"]
    ) == 9
    assert sum(
        oracle["kind"] == "tool_succeeded"
        for case in cases
        for oracle in case["oracles"]
    ) == 8
    assert sum(
        oracle["kind"] == "tool_failed"
        for case in cases
        for oracle in case["oracles"]
    ) == 2
    learning_cases = [
        case
        for case in cases
        if case["category"] == "persistent-memory-learning"
    ]
    assert all(
        "Your first tool call must be read_file" in case["turns"][0]["prompt"]
        for case in learning_cases
    )


def test_memory_compaction_addendum_freezes_four_cross_boundary_tasks() -> None:
    document = build_addendum_manifest()
    cases = _validate_manifest(document)

    assert len(cases) == 2
    assert sum(len(case["turns"]) for case in cases) == 4
    assert all(
        any(
            oracle["kind"] == "context_compaction_count"
            and oracle["min"] == 2
            for oracle in case["oracles"]
        )
        for case in cases
    )


def test_live_manifest_rejects_fixture_path_escape() -> None:
    with pytest.raises(ValueError, match="escapes workspace"):
        _safe_relative("../outside.py")


def test_tree_digest_ignores_runtime_memory_but_detects_source_edits(
    tmp_path: Path,
) -> None:
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    before = _tree_digest(tmp_path)
    memory = tmp_path / ".mini-code-memory" / "MEMORY.md"
    memory.parent.mkdir()
    memory.write_text("runtime projection", encoding="utf-8")
    ledger = tmp_path / ".mini-code" / "skill_versions.json"
    ledger.parent.mkdir()
    ledger.write_text("{}", encoding="utf-8")

    assert _tree_digest(tmp_path) == before
    skill = tmp_path / ".mini-code" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Demo", encoding="utf-8")
    assert _tree_digest(tmp_path) != before
    skill.unlink()
    source.write_text("value = 2\n", encoding="utf-8")
    assert _tree_digest(tmp_path) != before


def test_command_oracle_uses_argv_without_shell(tmp_path: Path) -> None:
    script = tmp_path / "check.py"
    script.write_text("raise SystemExit(0)\n", encoding="utf-8")

    assert _run_command_oracle(
        tmp_path,
        {
            "argv": ["{python}", "check.py"],
            "exitCode": 0,
            "timeoutSeconds": 5,
        },
    )


def test_isolated_write_approval_allows_only_workspace_edits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert _isolated_write_approval(
        workspace,
        {
            "kind": "edit",
            "review": {"targetPath": str(workspace / "northstar" / "core.py")},
        },
        (Path("northstar/core.py"),),
    ) == {"decision": "allow_turn"}
    assert _isolated_write_approval(
        workspace,
        {
            "kind": "edit",
            "review": {"targetPath": str(tmp_path / "outside.py")},
        },
        (Path("northstar/core.py"),),
    ) == {"decision": "deny_once"}
    assert _isolated_write_approval(
        workspace,
        {
            "kind": "command",
            "review": {
                "cwd": str(workspace),
                "command": "python",
                "args": ["-m", "unittest", "tests.test_targets"],
            },
        },
        (Path("northstar/core.py"),),
    ) == {"decision": "allow_once"}
    assert _isolated_write_approval(
        workspace,
        {
            "kind": "edit",
            "review": {"targetPath": str(workspace / "README.md")},
        },
        (),
    ) == {"decision": "deny_once"}
    assert _isolated_write_approval(
        workspace,
        {
            "kind": "command",
            "review": {
                "cwd": str(workspace),
                "command": "pytest",
                "args": ["tests"],
            },
        },
        (),
    ) == {"decision": "allow_once"}
    assert _isolated_write_approval(
        workspace,
        {
            "kind": "command",
            "review": {
                "cwd": str(workspace),
                "command": "python",
                "args": ["-c", "open('/tmp/escape', 'w').write('x')"],
            },
        },
        (Path("northstar/core.py"),),
    ) == {"decision": "deny_once"}


def test_checked_generated_manifest_matches_builder() -> None:
    path = Path(
        ".acceptance-work/2026-08-20-north-star-50/north-star-manifest-v2.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["suiteId"] == SUITE_ID
    assert document["cases"] == build_cases()
