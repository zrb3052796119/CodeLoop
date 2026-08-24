from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from minicode.mock_model import MockModelAdapter
from minicode.openai_adapter import OpenAIModelAdapter

from scripts.build_memory_compaction_north_star_manifest import (
    SUITE_ID as MEMORY_COMPACTION_SUITE_ID,
    build_addendum_manifest,
    build_manifest as build_memory_compaction_manifest,
)
from scripts.build_north_star_live_manifest import SUITE_ID, build_cases
from scripts.run_north_star_live import (
    TurnEvidence,
    _canonical_contract_sha256,
    _evaluate_oracle,
    _isolated_write_approval,
    _load_json_snapshot,
    _runtime_profile_contract,
    _runtime_profile_from_adapter,
    _runtime_profile_from_runtime,
    _run_turn,
    _run_command_oracle,
    _safe_relative,
    _seed_memory,
    _snapshot_memory_claim_types,
    _tree_digest,
    _validate_manifest,
    main as run_live_main,
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


def _fake_passed_case_result(
    case: dict,
    *,
    runtime_profile_sha256: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": str(case["id"]),
        "status": "passed",
        "verificationPassed": True,
        "unsafeActionCount": 0,
        "userInterventionCount": 0,
        "durationMs": 1,
        "modelCalls": 1,
        "inputTokens": 1,
        "outputTokens": 1,
        "runId": "run_" + "a" * 32,
        "passedOracleIds": list(case["oracleIds"]),
    }
    if runtime_profile_sha256 is not None:
        result["runtimeProfileSha256"] = runtime_profile_sha256
    return result


def _deepseek_runtime_contract() -> dict[str, object]:
    identity = {
        "adapterType": "openai_compatible",
        "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-pro",
        "provider": "custom",
    }
    return {
        **identity,
        "profileSha256": _canonical_contract_sha256(identity),
        "version": 2,
    }


def test_json_snapshot_parses_and_hashes_one_byte_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "manifest.json"
    first = b'{"schemaVersion":1,"suiteId":"first","cases":[]}'
    second = b'{"schemaVersion":1,"suiteId":"second","cases":[]}'
    reads: list[Path] = []

    def fake_read_bytes(candidate: Path) -> bytes:
        reads.append(candidate)
        return first if len(reads) == 1 else second

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)

    document, digest = _load_json_snapshot(path)

    assert reads == [path]
    assert document["suiteId"] == "first"
    assert digest == hashlib.sha256(first).hexdigest()


def test_runtime_profile_is_credential_free_and_contract_bound() -> None:
    profile = _runtime_profile_from_runtime(
        {
            "model": "deepseek-v4-pro",
            "provider": "custom",
            "customBaseUrl": "https://api.deepseek.com/v1",
            "customApiKey": "synthetic-secret-must-not-project",
        }
    )
    contract = _runtime_profile_contract(
        {"runtimeProfileContract": _deepseek_runtime_contract()}
    )

    assert profile == contract
    assert profile == {
        "adapterType": "openai_compatible",
        "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-pro",
        "profileSha256": _canonical_contract_sha256(
            {
                "adapterType": "openai_compatible",
                "endpointUrl": "https://api.deepseek.com/v1/chat/completions",
                "model": "deepseek-v4-pro",
                "provider": "custom",
            }
        ),
        "provider": "custom",
    }
    assert "secret" not in json.dumps(profile)

    invalid_contract = _deepseek_runtime_contract()
    invalid_contract["profileSha256"] = "0" * 64
    with pytest.raises(ValueError, match="runtime profile hash"):
        _runtime_profile_contract(
            {"runtimeProfileContract": invalid_contract}
        )

    invalid_version = _deepseek_runtime_contract()
    invalid_version["version"] = True
    with pytest.raises(ValueError, match="runtime profile contract"):
        _runtime_profile_contract(
            {"runtimeProfileContract": invalid_version}
        )


def test_turn_rejects_runtime_profile_mismatch_before_agent_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed: list[bool] = []

    class WrongRuntime:
        runtime = {
            "model": "different-model",
            "provider": "custom",
            "customBaseUrl": "https://api.deepseek.com/v1",
            "customApiKey": "synthetic-not-projected",
        }
        model = OpenAIModelAdapter(
            {
                "model": "different-model",
                "openaiBaseUrl": "https://api.deepseek.com/v1",
            },
            None,
        )

        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(
        "scripts.run_north_star_live.create_agent_turn_runtime",
        lambda **_kwargs: WrongRuntime(),
    )
    contract = _runtime_profile_contract(
        {"runtimeProfileContract": _deepseek_runtime_contract()}
    )
    assert contract is not None

    with pytest.raises(RuntimeError, match="runtime profile mismatch"):
        _run_turn(
            workspace=tmp_path,
            state_root=tmp_path / "state",
            journal_root=tmp_path / "journal",
            prompt="synthetic profile check",
            history=[],
            context_window=None,
            seed_entries=None,
            authorized_paths=(),
            runtime_profile_contract=contract,
        )

    assert disposed == [True]


def test_turn_rejects_mock_adapter_before_agent_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed: list[bool] = []

    class MockRuntime:
        runtime = {
            "model": "deepseek-v4-pro",
            "provider": "custom",
            "customBaseUrl": "https://api.deepseek.com/v1",
            "customApiKey": "synthetic-not-projected",
        }
        model = MockModelAdapter()

        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(
        "scripts.run_north_star_live.create_agent_turn_runtime",
        lambda **_kwargs: MockRuntime(),
    )
    contract = _runtime_profile_contract(
        {"runtimeProfileContract": _deepseek_runtime_contract()}
    )
    assert contract is not None

    with pytest.raises(RuntimeError, match="runtime adapter mismatch"):
        _run_turn(
            workspace=tmp_path,
            state_root=tmp_path / "state",
            journal_root=tmp_path / "journal",
            prompt="synthetic adapter check",
            history=[],
            context_window=None,
            seed_entries=None,
            authorized_paths=(),
            runtime_profile_contract=contract,
        )

    assert disposed == [True]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.deepseek.com:8443/v1",
        "https://api.deepseek.com/alternate",
    ],
)
def test_adapter_profile_binds_port_and_path(base_url: str) -> None:
    configured = {
        "model": "deepseek-v4-pro",
        "provider": "custom",
        "customBaseUrl": "https://api.deepseek.com/v1",
    }
    adapter = OpenAIModelAdapter(
        {"model": "deepseek-v4-pro", "openaiBaseUrl": base_url},
        None,
    )

    assert _runtime_profile_from_adapter(adapter, configured) != (
        _runtime_profile_from_runtime(configured)
    )


class _MemoryAttributionJournal:
    def __init__(
        self,
        *,
        written: dict[str, tuple[str, ...]] | None = None,
        rendered: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.written = written or {}
        self.rendered = rendered or {}

    def get_written_memory_ids(self, run_id: str) -> tuple[str, ...] | None:
        return self.written.get(run_id)

    def get_rendered_memory_ids(self, run_id: str) -> tuple[str, ...] | None:
        return self.rendered.get(run_id)


def test_memory_attributed_rejects_an_unrelated_warm_memory(
    tmp_path: Path,
) -> None:
    learning = replace(_turn_with_events(), run_id="run_" + "a" * 32)
    warm = replace(_turn_with_events(), run_id="run_" + "b" * 32)
    expected_id = "project-1787489278608289000-acecd352"
    unrelated_id = "project-1787489278615566000-477e0e50"
    journal = _MemoryAttributionJournal(
        written={learning.run_id: (expected_id,)},
        rendered={
            learning.run_id: (expected_id,),
            warm.run_id: (unrelated_id,),
        },
    )

    assert not _evaluate_oracle(
        {
            "kind": "memory_attributed",
            "source": "written",
            "sourceTurn": 0,
            "renderedTurn": 1,
            "claimType": "recovery",
            "min": 1,
        },
        workspace=tmp_path,
        turns=[learning, warm],
        before_digest="",
        journal=journal,
        memory_claim_types={expected_id: frozenset({"recovery"})},
    )


def test_memory_attributed_filters_sibling_lessons_by_claim_type(
    tmp_path: Path,
) -> None:
    learning = replace(_turn_with_events(), run_id="run_" + "d" * 32)
    warm = replace(_turn_with_events(), run_id="run_" + "e" * 32)
    verification_rule_id = "project-1787489278608289000-acecd352"
    approach_id = "project-1787489278615566000-477e0e50"
    oracle = {
        "kind": "memory_attributed",
        "source": "written",
        "sourceTurn": 0,
        "renderedTurn": 1,
        "claimType": "verification_rule",
        "min": 1,
    }
    claim_types = {
        verification_rule_id: frozenset({"verification_rule"}),
        approach_id: frozenset({"approach"}),
    }
    written = {learning.run_id: (verification_rule_id, approach_id)}

    assert not _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[learning, warm],
        before_digest="",
        journal=_MemoryAttributionJournal(
            written=written,
            rendered={warm.run_id: (approach_id,)},
        ),
        memory_claim_types=claim_types,
    )
    assert _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[learning, warm],
        before_digest="",
        journal=_MemoryAttributionJournal(
            written=written,
            rendered={warm.run_id: (verification_rule_id,)},
        ),
        memory_claim_types=claim_types,
    )


def test_memory_attributed_uses_the_source_turn_claim_snapshot(
    tmp_path: Path,
) -> None:
    expected_id = "project-1787489278608289000-acecd352"
    learning = replace(
        _turn_with_events(),
        run_id="run_" + "f" * 32,
        memory_claim_types=(
            (expected_id, frozenset({"verification_rule"})),
        ),
    )
    warm = replace(_turn_with_events(), run_id="run_" + "1" * 32)

    assert _evaluate_oracle(
        {
            "kind": "memory_attributed",
            "source": "written",
            "sourceTurn": 0,
            "renderedTurn": 1,
            "claimType": "verification_rule",
            "min": 1,
        },
        workspace=tmp_path,
        turns=[learning, warm],
        before_digest="",
        journal=_MemoryAttributionJournal(
            written={learning.run_id: (expected_id,)},
            rendered={warm.run_id: (expected_id,)},
        ),
    )


def test_memory_attributed_requires_the_exact_seeded_memory(
    tmp_path: Path,
) -> None:
    turn = replace(_turn_with_events(), run_id="run_" + "c" * 32)
    expected_id = "project-1787489333649285000-f0d092e2"
    unrelated_id = "project-1787489366167693000-18d7dd14"
    oracle = {
        "kind": "memory_attributed",
        "source": "seeded",
        "seedIndexes": [0],
        "renderedTurn": 0,
        "min": 1,
    }

    assert _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[turn],
        before_digest="",
        journal=_MemoryAttributionJournal(
            rendered={turn.run_id: (expected_id,)},
        ),
        seeded_memory_ids=(expected_id,),
    )
    assert not _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[turn],
        before_digest="",
        journal=_MemoryAttributionJournal(
            rendered={turn.run_id: (unrelated_id,)},
        ),
        seeded_memory_ids=(expected_id,),
    )


def test_seed_memory_returns_only_content_free_entry_ids(tmp_path: Path) -> None:
    from minicode.memory import MemoryManager

    manager = MemoryManager(project_root=tmp_path)
    seeded = _seed_memory(
        manager,
        [
            {
                "category": "project-constraint",
                "content": "Keep public response fields backward compatible.",
                "tags": ["compatibility"],
            }
        ],
    )

    assert len(seeded) == 1
    assert seeded[0].startswith("project-")
    assert "backward compatible" not in seeded[0]


def test_memory_claim_type_snapshot_exposes_only_id_to_type_bindings(
    tmp_path: Path,
) -> None:
    from minicode.memory import MemoryManager, MemoryScope

    manager = MemoryManager(project_root=tmp_path)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "task_context",
        "After changing the ledger, run its focused compatibility test.",
        metadata={
            "structured_reflection": {
                "claims": [
                    {"claim_type": "verification_rule"},
                    {"claim_type": "not-a-supported-claim"},
                ]
            }
        },
    )
    assert entry is not None

    snapshot = _snapshot_memory_claim_types(manager)

    assert snapshot == {entry.id: frozenset({"verification_rule"})}
    assert "focused compatibility test" not in repr(snapshot)


def test_live_manifest_rejects_memory_attribution_without_a_later_warm_turn() -> None:
    case = dict(build_cases()[0])
    case["turns"] = [{"prompt": "learn"}, {"prompt": "reuse"}]
    case["oracles"] = [
        {
            "id": "lesson-attributed",
            "kind": "memory_attributed",
            "source": "written",
            "sourceTurn": 1,
            "renderedTurn": 1,
            "claimType": "recovery",
            "min": 1,
        }
    ]
    case["oracleIds"] = ["lesson-attributed"]

    with pytest.raises(ValueError, match="memory_attributed"):
        _validate_manifest(
            {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [case]}
        )


@pytest.mark.parametrize(
    ("oracle", "memory_entries"),
    [
        (
            {
                "source": "written",
                "sourceTurn": 0,
                "renderedTurn": 1,
                "claimType": "invented_claim",
            },
            None,
        ),
        (
            {
                "source": "seeded",
                "seedIndexes": [1],
                "renderedTurn": 0,
            },
            [{"content": "only seed"}],
        ),
        (
            {
                "source": "seeded",
                "seedIndexes": [0],
                "renderedTurn": 0,
                "claimType": "constraint",
            },
            [{"content": "only seed"}],
        ),
        (
            {
                "source": "seeded",
                "seedIndexes": [0, 0],
                "renderedTurn": 0,
            },
            [{"content": "only seed"}],
        ),
        (
            {
                "source": "seeded",
                "seedIndexes": [0],
                "renderedTurn": 0,
                "min": 2,
            },
            [{"content": "only seed"}],
        ),
    ],
)
def test_live_manifest_rejects_malformed_memory_attribution_contracts(
    oracle: dict[str, object],
    memory_entries: list[dict[str, str]] | None,
) -> None:
    case = dict(build_cases()[0])
    case["turns"] = [{"prompt": "learn"}, {"prompt": "reuse"}]
    if memory_entries is None:
        case.pop("memoryEntries", None)
    else:
        case["memoryEntries"] = memory_entries
    case["oracles"] = [
        {"id": "lesson-attributed", "kind": "memory_attributed", **oracle}
    ]
    case["oracleIds"] = ["lesson-attributed"]

    with pytest.raises(ValueError, match="memory_attributed"):
        _validate_manifest(
            {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [case]}
        )


def test_live_manifest_accepts_written_and_seeded_memory_attribution() -> None:
    written = dict(build_cases()[0])
    written["turns"] = [{"prompt": "learn"}, {"prompt": "reuse"}]
    written.pop("memoryEntries", None)
    written["oracles"] = [
        {
            "id": "lesson-attributed",
            "kind": "memory_attributed",
            "source": "written",
            "sourceTurn": 0,
            "renderedTurn": 1,
            "claimType": "recovery",
            "min": 1,
        }
    ]
    written["oracleIds"] = ["lesson-attributed"]
    seeded = dict(build_cases()[1])
    seeded["turns"] = [{"prompt": "use seed"}]
    seeded["memoryEntries"] = [{"content": "bounded seed"}]
    seeded["oracles"] = [
        {
            "id": "lesson-attributed",
            "kind": "memory_attributed",
            "source": "seeded",
            "seedIndexes": [0],
            "renderedTurn": 0,
            "min": 1,
        }
    ]
    seeded["oracleIds"] = ["lesson-attributed"]

    validated = _validate_manifest(
        {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [written, seeded]}
    )

    assert [case["id"] for case in validated] == [written["id"], seeded["id"]]


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


def test_verification_passed_oracle_accepts_any_verified_tool_per_turn(
    tmp_path: Path,
) -> None:
    run_command_turn = _turn_with_events(
        (
            "task.verified",
            {"kind": "tests", "outcome": "passed", "source": "run_command_exit"},
        )
    )
    recovered_test_runner_turn = _turn_with_events(
        (
            "task.verified",
            {"kind": "tests", "outcome": "failed", "source": "test_runner"},
        ),
        (
            "task.verified",
            {"kind": "tests", "outcome": "passed", "source": "test_runner"},
        ),
    )
    failed_turn = _turn_with_events(
        (
            "task.verified",
            {"kind": "tests", "outcome": "failed", "source": "test_runner"},
        )
    )
    workflow_review_turn = _turn_with_events(
        (
            "task.verified",
            {"kind": "review", "outcome": "passed", "source": "workflow_review"},
        )
    )
    lint_turn = _turn_with_events(
        (
            "task.verified",
            {"kind": "lint", "outcome": "passed", "source": "run_command_exit"},
        )
    )
    oracle = {
        "kind": "verification_passed",
        "min": 1,
        "everyTurn": True,
        "verificationKind": "tests",
        "sources": ["run_command_exit", "test_runner"],
    }

    assert _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[run_command_turn, recovered_test_runner_turn],
        before_digest="",
        journal=object(),
    )
    assert not _evaluate_oracle(
        oracle,
        workspace=tmp_path,
        turns=[run_command_turn, failed_turn],
        before_digest="",
        journal=object(),
    )
    for unrelated in (workflow_review_turn, lint_turn):
        assert not _evaluate_oracle(
            oracle,
            workspace=tmp_path,
            turns=[unrelated],
            before_digest="",
            journal=object(),
        )


@pytest.mark.parametrize(
    "contract",
    [
        {"min": 0},
        {"min": True},
        {"everyTurn": "yes"},
        {"verificationKind": "tests", "sources": []},
        {"verificationKind": "invented", "sources": ["test_runner"]},
        {"verificationKind": "tests", "sources": ["workflow_review"]},
    ],
)
def test_live_manifest_rejects_invalid_verification_passed_oracles(
    contract: dict[str, object],
) -> None:
    case = dict(build_cases()[0])
    case["oracles"] = [
        {
            "id": "verification-ran",
            "kind": "verification_passed",
            "verificationKind": "tests",
            "sources": ["run_command_exit", "test_runner"],
            **contract,
        }
    ]
    case["oracleIds"] = ["verification-ran"]

    with pytest.raises(ValueError, match="verification_passed"):
        _validate_manifest(
            {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [case]}
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


def test_live_manifest_rejects_a_mismatched_declared_case_count() -> None:
    cases = build_cases()[:2]

    with pytest.raises(ValueError, match="caseCount"):
        _validate_manifest(
            {
                "schemaVersion": 1,
                "suiteId": "mismatched-case-count-suite",
                "caseCount": 3,
                "cases": cases,
            }
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("contextWindow", 2000.5, "contextWindow"),
        ("carryHistory", "false", "carryHistory"),
        (
            "initialHistory",
            [{"role": "user", "content": 7}],
            "initialHistory",
        ),
    ],
)
def test_live_manifest_rejects_noncanonical_turn_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    case = deepcopy(build_cases()[0])
    case["turns"][0][field] = value

    with pytest.raises(ValueError, match=message):
        _validate_manifest(
            {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [case]}
        )


@pytest.mark.parametrize(
    "oracle",
    [
        {"id": "empty-response", "kind": "response_contains", "values": []},
        {"id": "negative-subagent", "kind": "subagent_count", "min": -1},
    ],
)
def test_live_manifest_rejects_vacuous_oracle_contracts(
    oracle: dict[str, object],
) -> None:
    case = deepcopy(build_cases()[0])
    case["oracles"] = [oracle]
    case["oracleIds"] = [oracle["id"]]

    with pytest.raises(ValueError, match="oracle contract"):
        _validate_manifest(
            {"schemaVersion": 1, "suiteId": SUITE_ID, "cases": [case]}
        )


@pytest.mark.parametrize("mutation", ["prompt", "oracle", "fixture"])
def test_resume_rejects_changed_manifest_contract_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "resume-contract-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    executed: list[str] = []

    def fake_execute(candidate, **_kwargs):
        executed.append(str(candidate["id"]))
        return (_fake_passed_case_result(candidate), {})

    monkeypatch.setattr("scripts.run_north_star_live._execute_case", fake_execute)
    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    assert executed == [case["id"]]
    frozen_result = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(frozen_result["manifestSha256"]) == 64
    assert len(frozen_result["sourceCodeSha256"]) == 64
    assert len(frozen_result["results"][0]["caseContractSha256"]) == 64
    assert frozen_result["sourceReport"] == "manifest.json"
    assert frozen_result["manifestCaseCount"] == 1
    assert frozen_result["completedCaseCount"] == 1
    assert frozen_result["selectedCaseIds"] == [case["id"]]
    assert frozen_result["selectionStatus"] == "passed"
    assert frozen_result["suiteFinalized"] is True
    assert frozen_result["suiteComplete"] is True
    assert frozen_result["suiteStatus"] == "passed"
    assert str(tmp_path) not in json.dumps(frozen_result)

    changed = deepcopy(document)
    changed_case = changed["cases"][0]
    if mutation == "prompt":
        changed_case["turns"][0]["prompt"] += " changed"
    elif mutation == "oracle":
        changed_case["oracles"].append(
            {
                "id": "changed-oracle",
                "kind": "response_contains",
                "values": ["changed"],
            }
        )
        changed_case["oracleIds"].append("changed-oracle")
    else:
        changed_case["files"]["resume-contract.txt"] = "changed\n"
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    executed.clear()

    with pytest.raises(ValueError, match="manifestSha256"):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )
    assert executed == []


def test_resume_rejects_tampered_case_contract_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "resume-case-contract-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    def fake_execute(candidate, **_kwargs):
        return (_fake_passed_case_result(candidate), {})

    monkeypatch.setattr("scripts.run_north_star_live._execute_case", fake_execute)
    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    result["results"][0]["caseContractSha256"] = "0" * 64
    output_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda *_args, **_kwargs: pytest.fail("resume executed a case"),
    )

    with pytest.raises(ValueError, match="caseContractSha256"):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )


def test_resume_rejects_tampered_passed_oracles_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "resume-oracle-contract-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (
            _fake_passed_case_result(candidate),
            {},
        ),
    )
    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    result["results"][0]["passedOracleIds"].pop()
    output_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda *_args, **_kwargs: pytest.fail("resume executed a case"),
    )

    with pytest.raises(ValueError, match="resume result pass contract"):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )


def test_resume_rejects_coherent_public_only_failure_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "resume-evidence-authority-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    def failed_execute(candidate, **_kwargs):
        result = _fake_passed_case_result(candidate)
        result.update(
            {
                "status": "failed",
                "verificationPassed": False,
                "passedOracleIds": [],
            }
        )
        return result, {
            "failure": "RuntimeError",
            "oracleFailures": {},
            "runIds": [result["runId"]],
        }

    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        failed_execute,
    )
    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 1
    public = json.loads(output_path.read_text(encoding="utf-8"))
    public_case = public["results"][0]
    public_case["status"] = "passed"
    public_case["verificationPassed"] = True
    public_case["passedOracleIds"] = list(case["oracleIds"])
    public["selectionStatus"] = "passed"
    public["suiteStatus"] = "passed"
    output_path.write_text(json.dumps(public), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda *_args, **_kwargs: pytest.fail("resume executed a case"),
    )

    with pytest.raises(ValueError, match="pass contract.*evidence"):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )


def test_resume_rejects_changed_private_evidence_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "resume-private-evidence-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (_fake_passed_case_result(candidate), {}),
    )
    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    evidence_path = (
        tmp_path
        / "result-evidence"
        / "cases"
        / str(case["id"])
        / "evidence.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["modelCalls"] += 1
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="evidence identity"):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )


def test_private_evidence_is_minimal_and_uses_posix_owner_only_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "private-evidence-permissions-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (_fake_passed_case_result(candidate), {}),
    )

    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    evidence_path = (
        tmp_path
        / "result-evidence"
        / "cases"
        / str(case["id"])
        / "evidence.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert "responses" not in evidence
    if os.name == "posix":
        assert stat.S_IMODE(evidence_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(evidence_path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("target", ["case", "envelope"])
def test_resume_rejects_tampered_runtime_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    case = deepcopy(build_cases()[0])
    raw_contract = _deepseek_runtime_contract()
    profile = _runtime_profile_contract(
        {"runtimeProfileContract": raw_contract}
    )
    assert profile is not None
    document = {
        "schemaVersion": 1,
        "suiteId": "runtime-profile-suite",
        "runtimeProfileContract": raw_contract,
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (
            _fake_passed_case_result(
                candidate,
                runtime_profile_sha256=profile["profileSha256"],
            ),
            {},
        ),
    )

    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["runtimeProfile"] == profile
    if target == "case":
        result["results"][0]["runtimeProfileSha256"] = "0" * 64
        expected_error = "runtime profile identity"
    else:
        result["runtimeProfile"]["model"] = "different-model"
        expected_error = "suite envelope"
    output_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda *_args, **_kwargs: pytest.fail("resume executed a case"),
    )

    with pytest.raises(ValueError, match=expected_error):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )


@pytest.mark.parametrize(
    ("field", "replacement", "expected_error"),
    [
        ("schemaVersion", True, "schemaVersion"),
        ("manifestCaseCount", True, "suite envelope"),
        ("completedCaseCount", True, "suite envelope"),
        ("selectionComplete", 1, "suite envelope"),
        ("suiteComplete", 1, "suite envelope"),
        ("suiteFinalized", 1, "suite finalization"),
    ],
)
def test_resume_rejects_json_bool_integer_type_confusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
    expected_error: str,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "resume-scalar-type-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (
            _fake_passed_case_result(candidate),
            {},
        ),
    )
    assert run_live_main(
        ["--manifest", str(manifest_path), "--output", str(output_path)]
    ) == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    result[field] = replacement
    output_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        run_live_main(
            [
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--resume",
            ]
        )


def test_execution_aborts_if_source_changes_during_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = deepcopy(build_cases()[0])
    document = {
        "schemaVersion": 1,
        "suiteId": "source-identity-suite",
        "cases": [case],
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    source_hashes = iter(["a" * 64, "a" * 64, "b" * 64])
    monkeypatch.setattr(
        "scripts.run_north_star_live._source_code_sha256",
        lambda: next(source_hashes),
    )
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (
            _fake_passed_case_result(candidate),
            {},
        ),
    )

    with pytest.raises(RuntimeError, match="source changed"):
        run_live_main(
            ["--manifest", str(manifest_path), "--output", str(output_path)]
        )
    assert not output_path.exists()


def test_partial_selection_is_incomplete_until_full_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = deepcopy(build_cases()[:2])
    document = {
        "schemaVersion": 1,
        "suiteId": "selection-completeness-suite",
        "cases": cases,
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    executed: list[str] = []

    def fake_execute(candidate, **_kwargs):
        executed.append(str(candidate["id"]))
        return (_fake_passed_case_result(candidate), {})

    monkeypatch.setattr("scripts.run_north_star_live._execute_case", fake_execute)

    assert run_live_main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--limit",
            "1",
        ]
    ) == 1
    partial = json.loads(output_path.read_text(encoding="utf-8"))
    assert executed == [cases[0]["id"]]
    assert partial["manifestCaseCount"] == 2
    assert partial["selectedCaseIds"] == [cases[0]["id"]]
    assert partial["completedCaseCount"] == 1
    assert partial["selectionComplete"] is True
    assert partial["selectionStatus"] == "passed"
    assert partial["suiteFinalized"] is True
    assert partial["suiteComplete"] is False
    assert partial["suiteStatus"] == "incomplete"

    executed.clear()
    assert run_live_main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--resume",
        ]
    ) == 0
    complete = json.loads(output_path.read_text(encoding="utf-8"))
    assert executed == [cases[1]["id"]]
    assert complete["selectedCaseIds"] == [case["id"] for case in cases]
    assert complete["completedCaseCount"] == 2
    assert complete["selectionStatus"] == "passed"
    assert complete["suiteFinalized"] is True
    assert complete["suiteComplete"] is True
    assert complete["suiteStatus"] == "passed"


def test_second_case_source_drift_leaves_only_an_incomplete_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = deepcopy(build_cases()[:2])
    document = {
        "schemaVersion": 1,
        "suiteId": "mid-suite-source-identity-suite",
        "cases": cases,
    }
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "result.json"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    source_hashes = iter(["a" * 64] * 4 + ["b" * 64])
    monkeypatch.setattr(
        "scripts.run_north_star_live._source_code_sha256",
        lambda: next(source_hashes),
    )
    monkeypatch.setattr(
        "scripts.run_north_star_live._execute_case",
        lambda candidate, **_kwargs: (
            _fake_passed_case_result(candidate),
            {},
        ),
    )

    with pytest.raises(RuntimeError, match="source changed"):
        run_live_main(
            ["--manifest", str(manifest_path), "--output", str(output_path)]
        )

    partial = json.loads(output_path.read_text(encoding="utf-8"))
    assert [result["id"] for result in partial["results"]] == [cases[0]["id"]]
    assert partial["completedCaseCount"] == 1
    assert partial["suiteFinalized"] is False
    assert partial["suiteComplete"] is False
    assert partial["suiteStatus"] == "incomplete"


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
    embedding_cache = tmp_path / ".mini-code" / "skill-embeddings.json"
    embedding_cache.write_text("{}", encoding="utf-8")

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
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "agent_quality"
        / "north-star-manifest.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["suiteId"] == SUITE_ID
    assert document["cases"] == build_cases()
