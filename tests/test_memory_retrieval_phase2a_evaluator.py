from __future__ import annotations

import copy
import hashlib
import json
import shutil
import socket
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from scripts.memory_retrieval_evaluator import PHASE2A_ARMS
from scripts.memory_retrieval_phase2a_evaluator import (
    UNIFIED_ENTRYPOINTS,
    deterministic_phase2a_view,
    evaluate_phase2a_dataset,
    render_phase2a_comparison,
    render_phase2a_markdown,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests" / "fixtures" / "memory_retrieval_golden"
BASELINE = ROOT / "artifacts" / "memory-retrieval-baseline.json"
ORIGINAL_PHASE2A_FROZEN_HASHES = {
    "artifacts/memory-retrieval-phase2a.json": "2f488120e4016d9fafb275cd2b22b7e978ddf8f4039b990aeff1724e00759327",
    "docs/memory-retrieval-phase2a-comparison.md": "4c148cbe54f4e3d39ed5f2e1726f8ba7ee465b93d9329d7f39d884c0fa66e3fe",
    "docs/memory-retrieval-phase2a.md": "7414300118d678bbf7d1e1c9eba91c473d11044b83fc19d4ebc7f705d702b09b",
    "scripts/evaluate_memory_retrieval_phase2a.py": "6371ea3da21fe40845c588ece56679d451ab087d9acf8fa64aa8691a4fbae1ad",
    "scripts/memory_retrieval_evaluator.py": "70178d0bda4f705ff59ecb31602179cdb1f3901896aa688f00d95ddf88701389",
    "scripts/memory_retrieval_phase2a_evaluator.py": "f0ac492f8ab0d83055cc1e78ada4d38fa249276228e57f3dfc5fd6eacdd3ca3e",
    "tests/test_memory_retrieval_phase2a.py": "f5ec44edf9cac7191fc0960dec5992814899864a4fdeb4600dcfcef5fdd25f6f",
    "tests/test_memory_retrieval_phase2a_evaluator.py": "ad4693f597b1dbb754520ee883b36fc78b9d4f9e257f79e0b88a6251dd45b0ae",
}
ORIGINAL_PHASE2B_FROZEN_HASHES = {
    "artifacts/memory-retrieval-phase2b.json": "2d082e1aa50c1461a78ef5e18c56b59533460a140634effb911fd6c5b4bd3996",
    "artifacts/memory-retrieval-phase2b.schema.json": "a0a9a8093e9970d1fcd275f9d7670804b8b2ecd67ec468b45c13b5ee3390820a",
    "docs/memory-retrieval-phase2b-comparison.md": "6e2649e0345f6ec58433d3863a160e8cceb8e8828253cfec842faf35951113e5",
    "docs/memory-retrieval-phase2b-performance.md": "3cff028426be913baa06cacbd2eff69b3141f74ff16528d5e44b4f37416a5235",
    "docs/memory-retrieval-phase2b.md": "9ec83beff0ab5a5c0b2af3fd65e62f37b441a4416e556b98c751032e51027da9",
    "scripts/evaluate_memory_retrieval_phase2b.py": "841883544b031ff5b58ea759a2688413637e70143cd231708514843700ed05dd",
    "scripts/memory_retrieval_phase2b_evaluator.py": "d7ab07c72795b2cb49afd1b7235d88ab94dbb2ca60258540b3d84d17f93de785",
    "tests/fixtures/memory_retrieval_phase2b_holdout.json": "5ceb46134d0d17060c7b635bb99aeae8a43c799a3f6dd40a07d65978930b1136",
    "tests/fixtures/memory_retrieval_phase2b_holdout.schema.json": "c1d4461fcf2e23949585d0742fd20af4d2486d05f1406ad3469c204a21a83ae4",
    "tests/test_memory_candidate_consolidation.py": "4c7011ba7168388b88fc58a3fe253366a3d5c19dd68dac36c50c8febdf4de67c",
    "tests/test_memory_retrieval_phase2b.py": "496882681aaa5d3281b66669d4d4b8a31a785386400d02a1009e6cee59b8548b",
    "tests/test_memory_retrieval_phase2b_evaluator.py": "828bf028c91ed00c6d3d103d4d84e8c5632a0fddd28022b0c6cc11af3f8537c3",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def _report() -> dict:
    return evaluate_phase2a_dataset(
        DATASET,
        project_root=ROOT,
        baseline_path=BASELINE,
    )


def test_advisory_policy_keeps_wall_clock_failure_observable() -> None:
    from scripts.memory_retrieval_phase2a_evaluator import (
        evaluate_phase2a_performance_policy,
    )

    policy = evaluate_phase2a_performance_policy(
        canonical_p95_ms=5.000001,
        task_start_average_saves=2,
        turn_total_average_saves=3,
        enforcement_mode="advisory",
    )

    assert policy == {
        "enforcementMode": "advisory",
        "deterministicGates": {
            "task_start_average_saves_at_most_2": True,
            "turn_total_average_saves_at_most_3": True,
        },
        "wallClockGates": {"canonical_p95_at_most_5_ms": False},
        "deterministicPassed": True,
        "strictPassed": False,
        "acceptancePassed": True,
    }


@pytest.mark.parametrize(
    ("mode", "canonical_p95_ms", "expected_strict", "expected_acceptance"),
    [
        ("advisory", 5.0, True, True),
        ("advisory", 5.000001, False, True),
        ("strict", 5.0, True, True),
        ("strict", 5.000001, False, False),
    ],
)
def test_performance_policy_enforces_exact_wall_clock_boundary(
    mode: str,
    canonical_p95_ms: float,
    expected_strict: bool,
    expected_acceptance: bool,
) -> None:
    from scripts.memory_retrieval_phase2a_evaluator import (
        evaluate_phase2a_performance_policy,
    )

    policy = evaluate_phase2a_performance_policy(
        canonical_p95_ms=canonical_p95_ms,
        task_start_average_saves=2,
        turn_total_average_saves=3,
        enforcement_mode=mode,
    )

    assert policy["wallClockGates"] == {
        "canonical_p95_at_most_5_ms": expected_strict
    }
    assert policy["strictPassed"] is expected_strict
    assert policy["acceptancePassed"] is expected_acceptance


@pytest.mark.parametrize(
    ("task_start_saves", "turn_total_saves"),
    [(2.000001, 3), (2, 3.000001)],
)
def test_performance_policy_fails_acceptance_when_deterministic_budget_exceeded(
    task_start_saves: float,
    turn_total_saves: float,
) -> None:
    from scripts.memory_retrieval_phase2a_evaluator import (
        evaluate_phase2a_performance_policy,
    )

    for mode in ("advisory", "strict"):
        policy = evaluate_phase2a_performance_policy(
            canonical_p95_ms=5,
            task_start_average_saves=task_start_saves,
            turn_total_average_saves=turn_total_saves,
            enforcement_mode=mode,
        )
        assert policy["deterministicPassed"] is False
        assert policy["strictPassed"] is False
        assert policy["acceptancePassed"] is False


def test_performance_policy_rejects_missing_input() -> None:
    from scripts.memory_retrieval_phase2a_evaluator import (
        evaluate_phase2a_performance_policy,
    )

    with pytest.raises(TypeError):
        evaluate_phase2a_performance_policy(  # type: ignore[call-arg]
            canonical_p95_ms=5,
            task_start_average_saves=2,
            enforcement_mode="advisory",
        )


@pytest.mark.parametrize("mode", ["", "unknown", None, []])
def test_performance_policy_rejects_unknown_or_unhashable_mode(
    mode: object,
) -> None:
    from scripts.memory_retrieval_phase2a_evaluator import (
        evaluate_phase2a_performance_policy,
    )

    with pytest.raises(ValueError, match="unsupported enforcement_mode"):
        evaluate_phase2a_performance_policy(
            canonical_p95_ms=5,
            task_start_average_saves=2,
            turn_total_average_saves=3,
            enforcement_mode=mode,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_p95_ms", True),
        ("canonical_p95_ms", float("nan")),
        ("canonical_p95_ms", float("inf")),
        ("canonical_p95_ms", -0.1),
        ("canonical_p95_ms", "5"),
        ("task_start_average_saves", False),
        ("task_start_average_saves", float("nan")),
        ("task_start_average_saves", float("-inf")),
        ("task_start_average_saves", -1),
        ("task_start_average_saves", "2"),
        ("turn_total_average_saves", True),
        ("turn_total_average_saves", float("nan")),
        ("turn_total_average_saves", float("inf")),
        ("turn_total_average_saves", -1),
        ("turn_total_average_saves", object()),
    ],
)
def test_performance_policy_rejects_invalid_metrics(
    field: str,
    value: object,
) -> None:
    from scripts.memory_retrieval_phase2a_evaluator import (
        evaluate_phase2a_performance_policy,
    )

    values: dict[str, object] = {
        "canonical_p95_ms": 5,
        "task_start_average_saves": 2,
        "turn_total_average_saves": 3,
    }
    values[field] = value

    with pytest.raises(ValueError, match="finite non-negative number"):
        evaluate_phase2a_performance_policy(
            canonical_p95_ms=values["canonical_p95_ms"],  # type: ignore[arg-type]
            task_start_average_saves=values[  # type: ignore[arg-type]
                "task_start_average_saves"
            ],
            turn_total_average_saves=values[  # type: ignore[arg-type]
                "turn_total_average_saves"
            ],
            enforcement_mode="advisory",
        )


def test_timing_free_projection_normalizes_all_wall_clock_derivatives() -> None:
    passing = {
        "latency": {"canonical_retrieval": {"p50_ms": 4.0, "p95_ms": 5.0}},
        "per_case_results": [
            {"arms": {"canonical_retrieval": {"latency_ms": 4.5, "selected_ids": ["m1"]}}}
        ],
        "correctness_gates": {"correct": True},
        "quality_gates": {"quality": True},
        "integrity_gates": {"integrity": True},
        "performance_gates": {
            "canonical_p95_at_most_5_ms": True,
            "task_start_average_saves_at_most_2": True,
            "turn_total_average_saves_at_most_3": True,
        },
        "performancePolicy": {
            "enforcementMode": "strict",
            "deterministicGates": {
                "task_start_average_saves_at_most_2": True,
                "turn_total_average_saves_at_most_3": True,
            },
            "wallClockGates": {"canonical_p95_at_most_5_ms": True},
            "deterministicPassed": True,
            "strictPassed": True,
            "acceptancePassed": True,
        },
        "enforcementMode": "strict",
        "deterministicPassed": True,
        "deterministicAcceptancePassed": True,
        "strictPassed": True,
        "acceptancePassed": True,
    }
    failing = copy.deepcopy(passing)
    failing["latency"]["canonical_retrieval"] = {"p50_ms": 5.1, "p95_ms": 5.2}
    failing["per_case_results"][0]["arms"]["canonical_retrieval"]["latency_ms"] = 5.15
    failing["performance_gates"]["canonical_p95_at_most_5_ms"] = False
    failing["performancePolicy"]["wallClockGates"]["canonical_p95_at_most_5_ms"] = False
    failing["performancePolicy"]["strictPassed"] = False
    failing["performancePolicy"]["acceptancePassed"] = False
    failing["strictPassed"] = False
    failing["acceptancePassed"] = False

    assert deterministic_phase2a_view(passing) == deterministic_phase2a_view(failing)


def test_timing_free_projection_preserves_deterministic_gate_differences() -> None:
    passing = {
        "performance_gates": {
            "canonical_p95_at_most_5_ms": True,
            "task_start_average_saves_at_most_2": True,
        },
        "performancePolicy": {
            "enforcementMode": "advisory",
            "deterministicGates": {
                "task_start_average_saves_at_most_2": True,
            },
            "wallClockGates": {"canonical_p95_at_most_5_ms": True},
            "deterministicPassed": True,
            "strictPassed": True,
            "acceptancePassed": True,
        },
        "deterministicPassed": True,
        "deterministicAcceptancePassed": True,
        "acceptancePassed": True,
    }
    failing = copy.deepcopy(passing)
    failing["performance_gates"]["task_start_average_saves_at_most_2"] = False
    failing["performancePolicy"]["deterministicGates"][
        "task_start_average_saves_at_most_2"
    ] = False
    failing["performancePolicy"]["deterministicPassed"] = False
    failing["performancePolicy"]["acceptancePassed"] = False
    failing["deterministicPassed"] = False
    failing["deterministicAcceptancePassed"] = False
    failing["acceptancePassed"] = False

    assert deterministic_phase2a_view(passing) != deterministic_phase2a_view(failing)


def test_report_has_five_arms_and_frozen_phase1_comparison() -> None:
    report = _report()

    assert report["dataset_case_count"] == 80
    assert tuple(report["arms"]) == PHASE2A_ARMS
    assert report["phase1_baseline_unchanged"] is True
    assert report["fixtures_unchanged"] is True
    assert report["protected_files_unchanged"] is True
    assert set(report["overall_metrics"]) == set(PHASE2A_ARMS)


def test_default_report_uses_advisory_policy_and_preserves_real_observation() -> None:
    report = _report()
    policy = report["performancePolicy"]

    assert report["enforcementMode"] == "advisory"
    assert policy["enforcementMode"] == "advisory"
    assert report["performanceGatesRole"] == "legacy_observation_only"
    assert report["latency"]["canonical_retrieval"]["p95_ms"] >= 0
    assert policy["wallClockGates"] == {
        "canonical_p95_at_most_5_ms": report["latency"]["canonical_retrieval"][
            "p95_ms"
        ]
        <= 5.0
    }
    assert report["deterministicPassed"] is policy["deterministicPassed"]
    assert report["strictPassed"] is policy["strictPassed"]
    assert report["acceptancePassed"] is report["deterministicAcceptancePassed"]


def test_all_deterministic_phase2a_acceptance_gate_groups_pass() -> None:
    report = _report()

    assert all(report["correctness_gates"].values())
    assert all(report["quality_gates"].values())
    assert all(report["integrity_gates"].values())
    assert all(report["performancePolicy"]["deterministicGates"].values())
    assert report["deterministicAcceptancePassed"] is True
    assert report["acceptancePassed"] is True


def test_unified_entrypoints_have_identical_top1_for_every_case() -> None:
    agreement = _report()["entrypoint_agreement"]

    assert tuple(agreement["entrypoints"]) == UNIFIED_ENTRYPOINTS
    assert agreement["top1_agreement_rate"] == 1.0
    assert agreement["disagreements"] == []


def test_pipeline_identity_views_and_hard_budgets_are_truthful() -> None:
    report = _report()
    metrics = report["overall_metrics"]["pipeline_inject"]

    assert metrics["returned_rendered_disagreement_count"] == 0
    assert metrics["rendered_recorded_disagreement_count"] == 0
    assert metrics["rendered_feedback_disagreement_count"] == 0
    assert metrics["max_memories_violation_count"] == 0
    assert metrics["token_budget_violation_count"] == 0
    assert metrics["inactive_memory_leakage_count"] == 0


def test_quality_improves_without_falling_below_recall_floor() -> None:
    report = _report()
    phase1 = report["phase1_overall_metrics"]["pipeline_inject"]
    phase2a = report["overall_metrics"]["pipeline_inject"]

    assert phase2a["negative_false_injection_rate"] == 0.0
    assert phase2a["recall_at_5"] >= 0.95
    assert phase2a["actual_rendered_precision"] > phase1["actual_rendered_precision"]
    assert phase2a["must_exclude_violation_rate"] < phase1["must_exclude_violation_rate"]


def test_pipeline_io_meets_limits_and_real_latency_remains_observable() -> None:
    report = _report()
    pipeline_io = report["save_io"]["pipeline_inject"]

    assert pipeline_io["average_task_start_scope_saves"] <= 2
    assert pipeline_io["average_total_scope_saves"] <= 3
    assert isinstance(report["latency"]["canonical_retrieval"]["p95_ms"], float)
    assert report["latency"]["canonical_retrieval"]["p95_ms"] >= 0


def test_phase2a_cli_exit_policy_is_advisory_by_default_and_strict_on_request() -> None:
    from scripts.memory_retrieval_phase2a_evaluator import phase2a_exit_code

    advisory_wall_clock_failure = {
        "correctness_gates": {"correct": True},
        "quality_gates": {"quality": True},
        "integrity_gates": {"integrity": True},
        "performancePolicy": {
            "enforcementMode": "advisory",
            "deterministicPassed": True,
            "strictPassed": False,
            "acceptancePassed": True,
        },
        "enforcementMode": "advisory",
        "deterministicPassed": True,
        "deterministicAcceptancePassed": True,
        "strictPassed": False,
        "acceptancePassed": True,
    }
    strict_wall_clock_failure = copy.deepcopy(advisory_wall_clock_failure)
    strict_wall_clock_failure["performancePolicy"]["enforcementMode"] = "strict"
    strict_wall_clock_failure["performancePolicy"]["acceptancePassed"] = False
    strict_wall_clock_failure["enforcementMode"] = "strict"
    strict_wall_clock_failure["acceptancePassed"] = False
    strict_wall_clock_pass = copy.deepcopy(strict_wall_clock_failure)
    strict_wall_clock_pass["performancePolicy"]["strictPassed"] = True
    strict_wall_clock_pass["performancePolicy"]["acceptancePassed"] = True
    strict_wall_clock_pass["strictPassed"] = True
    strict_wall_clock_pass["acceptancePassed"] = True

    assert phase2a_exit_code(advisory_wall_clock_failure) == 0
    assert phase2a_exit_code(strict_wall_clock_failure) == 1
    assert phase2a_exit_code(strict_wall_clock_pass) == 0
    assert phase2a_exit_code({}) == 1


def test_report_contains_score_no_match_identity_and_suppression_diagnostics() -> None:
    report = _report()
    canonical = report["per_case_results"][0]["arms"]["canonical_retrieval"]

    assert canonical["candidate_ids"]
    assert isinstance(canonical["selected_ids"], list)
    assert isinstance(canonical["rendered_ids"], list)
    assert canonical["score_breakdown"][0]["score"]["final_score"] >= 0
    assert "no_match_reason" in canonical
    assert report["suppressed_reason_counts"]["canonical_retrieval"]


def test_two_evaluations_have_identical_timing_free_core() -> None:
    first = evaluate_phase2a_dataset(
        DATASET,
        project_root=ROOT,
        baseline_path=BASELINE,
    )
    second = evaluate_phase2a_dataset(
        DATASET,
        project_root=ROOT,
        baseline_path=BASELINE,
    )

    assert deterministic_phase2a_view(first) == deterministic_phase2a_view(second)


def test_phase2a_evaluator_never_connects_to_network(monkeypatch) -> None:
    calls: list[object] = []

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    evaluate_phase2a_dataset(
        DATASET,
        project_root=ROOT,
        baseline_path=BASELINE,
    )

    assert calls == []


def test_markdown_reports_include_metrics_delta_and_limits() -> None:
    phase2a = render_phase2a_markdown(_report())
    comparison = render_phase2a_comparison(_report())

    assert "Five Arms" in phase2a
    assert "Identity And Ownership" in phase2a
    assert "Limits" in phase2a
    assert "Mode: `advisory`" in phase2a
    assert "Canonical P95 measured:" in phase2a
    assert "Canonical P95 limit: `5.0 ms`" in phase2a
    assert "Wall-clock gate:" in phase2a
    assert "Deterministic acceptance:" in phase2a
    assert "Strict performance result:" in phase2a
    assert "Advisory acceptance does not claim" in phase2a
    assert "Absolute delta" in comparison
    assert "Phase 1 Pipeline Inject" in comparison


def test_cli_writes_only_new_parseable_reports(tmp_path: Path) -> None:
    output = tmp_path / "phase2a.json"
    markdown = tmp_path / "phase2a.md"
    comparison = tmp_path / "comparison.md"
    core = tmp_path / "core.json"
    baseline_before = BASELINE.read_bytes()
    command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_memory_retrieval_phase2a.py"),
        "--output",
        str(output),
        "--markdown",
        str(markdown),
        "--comparison",
        str(comparison),
        "--deterministic-core-output",
        str(core),
    ]

    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["dataset_case_count"] == 80
    assert json.loads(core.read_text(encoding="utf-8"))["remote_call_count"] == 0
    assert "Five Arms" in markdown.read_text(encoding="utf-8")
    assert "Absolute delta" in comparison.read_text(encoding="utf-8")
    assert BASELINE.read_bytes() == baseline_before


def test_cli_defaults_to_advisory_generated_output_paths() -> None:
    from scripts import evaluate_memory_retrieval_phase2a as cli

    args = cli.parse_args([])

    assert args.enforce_wall_clock_performance is False
    assert args.output == ROOT / "artifacts" / "memory-retrieval-phase2a-evaluation.json"
    assert args.markdown == ROOT / "docs" / "memory-retrieval-phase2a-evaluation.md"
    assert (
        args.comparison
        == ROOT / "docs" / "memory-retrieval-phase2a-evaluation-comparison.md"
    )

    strict_args = cli.parse_args(["--enforce-wall-clock-performance"])
    assert strict_args.enforce_wall_clock_performance is True


@pytest.mark.parametrize(
    ("option", "frozen_path"),
    [
        ("--output", ROOT / "artifacts" / "memory-retrieval-phase2a.json"),
        ("--markdown", ROOT / "docs" / "memory-retrieval-phase2a.md"),
        (
            "--comparison",
            ROOT / "docs" / "memory-retrieval-phase2a-comparison.md",
        ),
    ],
)
def test_cli_rejects_frozen_output_paths_without_touching_them(
    option: str,
    frozen_path: Path,
    tmp_path: Path,
) -> None:
    frozen_before = (frozen_path.read_bytes(), frozen_path.stat())
    command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_memory_retrieval_phase2a.py"),
        "--output",
        str(tmp_path / "phase2a.json"),
        "--markdown",
        str(tmp_path / "phase2a.md"),
        "--comparison",
        str(tmp_path / "comparison.md"),
        option,
        str(frozen_path),
    ]

    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )

    assert completed.returncode != 0
    assert "frozen accepted Phase 2A path" in completed.stderr
    assert frozen_path.read_bytes() == frozen_before[0]
    after_stat = frozen_path.stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        frozen_before[1].st_size,
        frozen_before[1].st_mtime_ns,
    )


def test_cli_rejects_unknown_arguments() -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_memory_retrieval_phase2a.py"),
        "--not-a-phase2a-option",
    ]

    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )

    assert completed.returncode == 2
    assert "unrecognized arguments" in completed.stderr


def test_default_certification_sources_do_not_assert_real_phase2a_p95_passes() -> None:
    targets = (
        ROOT / "tests" / "test_memory_retrieval_phase2a.py",
        ROOT / "tests" / "test_memory_retrieval_phase2a_evaluator.py",
        ROOT / "tests" / "test_memory_retrieval_phase2b.py",
        ROOT / "tests" / "test_memory_retrieval_phase2b_evaluator.py",
        ROOT / "tests" / "test_memory_retrieval_semantic_gap_evaluator.py",
        ROOT / "tests" / "test_memory_retrieval_baseline_contract.py",
    )
    forbidden_real_p95_assertion = (
        'assert report["latency"]["canonical_retrieval"]["p95_ms"] ' + "<= 5"
    )
    forbidden_legacy_authority = (
        'assert all(report["performance_' + 'gates"].values())'
    )

    for path in targets:
        source = path.read_text(encoding="utf-8")
        assert forbidden_real_p95_assertion not in source
        assert forbidden_legacy_authority not in source

    cli_source = (
        ROOT / "scripts" / "evaluate_memory_retrieval_phase2a.py"
    ).read_text(encoding="utf-8")
    assert "performance_" + "gates" not in cli_source


def test_phase2a_pin_cascade_has_exact_hardening_changed_set() -> None:
    from scripts.memory_retrieval_phase2b_evaluator import PHASE2A_FROZEN_HASHES

    actual = {
        relative: _sha256(ROOT / relative)
        for relative in ORIGINAL_PHASE2A_FROZEN_HASHES
    }
    changed = {
        relative
        for relative, original in ORIGINAL_PHASE2A_FROZEN_HASHES.items()
        if actual[relative] != original
    }

    assert changed == {
        "scripts/evaluate_memory_retrieval_phase2a.py",
        "scripts/memory_retrieval_phase2a_evaluator.py",
        "tests/test_memory_retrieval_phase2a_evaluator.py",
    }
    assert PHASE2A_FROZEN_HASHES == actual


def test_phase2b_evaluator_change_is_phase2a_pin_only_and_semantic_pin_is_exact() -> None:
    from scripts.memory_retrieval_phase2b_evaluator import PHASE2A_FROZEN_HASHES
    from scripts.memory_retrieval_semantic_gap_evaluator import (
        PHASE2B_FROZEN_HASHES,
    )

    phase2b_source = (
        ROOT / "scripts" / "memory_retrieval_phase2b_evaluator.py"
    ).read_bytes()
    normalized_phase2b = phase2b_source
    for relative in (
        "scripts/evaluate_memory_retrieval_phase2a.py",
        "scripts/memory_retrieval_phase2a_evaluator.py",
        "tests/test_memory_retrieval_phase2a_evaluator.py",
    ):
        normalized_phase2b = normalized_phase2b.replace(
            PHASE2A_FROZEN_HASHES[relative].encode(),
            ORIGINAL_PHASE2A_FROZEN_HASHES[relative].encode(),
        )
    assert (
        hashlib.sha256(normalized_phase2b).hexdigest()
        == ORIGINAL_PHASE2B_FROZEN_HASHES[
            "scripts/memory_retrieval_phase2b_evaluator.py"
        ]
    )

    actual_phase2b = {
        relative: _sha256(ROOT / relative)
        for relative in ORIGINAL_PHASE2B_FROZEN_HASHES
    }
    assert {
        relative
        for relative, original in ORIGINAL_PHASE2B_FROZEN_HASHES.items()
        if actual_phase2b[relative] != original
    } == {"scripts/memory_retrieval_phase2b_evaluator.py"}
    assert PHASE2B_FROZEN_HASHES == actual_phase2b

    semantic_source = (
        ROOT / "scripts" / "memory_retrieval_semantic_gap_evaluator.py"
    ).read_bytes()
    normalized_semantic = semantic_source.replace(
        PHASE2B_FROZEN_HASHES[
            "scripts/memory_retrieval_phase2b_evaluator.py"
        ].encode(),
        ORIGINAL_PHASE2B_FROZEN_HASHES[
            "scripts/memory_retrieval_phase2b_evaluator.py"
        ].encode(),
    )
    assert (
        hashlib.sha256(normalized_semantic).hexdigest()
        == "2ca6bfe6a232b743adf7796238b513a7e52466c99c28b3dd8737dbb75693512e"
    )


def test_phase2a_and_phase2b_tampering_reports_only_target_without_rewrite(
    tmp_path: Path,
) -> None:
    from scripts.memory_retrieval_phase2b_evaluator import (
        PHASE2A_FROZEN_HASHES,
        _hash_paths,
    )
    from scripts.memory_retrieval_semantic_gap_evaluator import (
        PHASE2B_FROZEN_HASHES,
        hash_paths,
    )

    phase2a_root = tmp_path / "phase2a"
    for relative in PHASE2A_FROZEN_HASHES:
        destination = phase2a_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    phase2a_accepted = (
        phase2a_root / "artifacts" / "memory-retrieval-phase2a.json"
    )
    accepted_before = phase2a_accepted.read_bytes()
    phase2a_target = (
        phase2a_root / "scripts" / "memory_retrieval_phase2a_evaluator.py"
    )
    phase2a_target.write_bytes(phase2a_target.read_bytes() + b"\n# controlled tamper\n")

    phase2a_result = _hash_paths(phase2a_root, PHASE2A_FROZEN_HASHES)

    assert set(phase2a_result["mismatches"]) == {
        "scripts/memory_retrieval_phase2a_evaluator.py"
    }
    assert phase2a_accepted.read_bytes() == accepted_before

    phase2b_root = tmp_path / "phase2b"
    for relative in PHASE2B_FROZEN_HASHES:
        destination = phase2b_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    phase2b_accepted = (
        phase2b_root / "artifacts" / "memory-retrieval-phase2b.json"
    )
    phase2b_accepted_before = phase2b_accepted.read_bytes()
    phase2b_target = (
        phase2b_root / "scripts" / "memory_retrieval_phase2b_evaluator.py"
    )
    phase2b_target.write_bytes(phase2b_target.read_bytes() + b"\n# controlled tamper\n")

    phase2b_result = hash_paths(phase2b_root, PHASE2B_FROZEN_HASHES)

    assert set(phase2b_result["mismatches"]) == {
        "scripts/memory_retrieval_phase2b_evaluator.py"
    }
    assert phase2b_accepted.read_bytes() == phase2b_accepted_before
