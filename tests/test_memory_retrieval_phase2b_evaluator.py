from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

from scripts.memory_retrieval_phase2b_evaluator import (
    PHASE1_FROZEN_HASHES,
    PHASE2A_FROZEN_HASHES,
    PHASE2A_P95_MATERIAL_LIMIT_MS,
    deterministic_phase2b_view,
    evaluate_performance_policy,
    evaluate_phase2b,
    phase2b_exit_code,
)


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "tests" / "fixtures" / "memory_retrieval_phase2b_holdout.json"
HOLDOUT_SCHEMA = ROOT / "tests" / "fixtures" / "memory_retrieval_phase2b_holdout.schema.json"
ARTIFACT_SCHEMA = ROOT / "artifacts" / "memory-retrieval-phase2b.schema.json"
PHASE1_DATASET = ROOT / "tests" / "fixtures" / "memory_retrieval_golden"
PHASE2A_BASELINE = ROOT / "artifacts" / "memory-retrieval-baseline.json"


@pytest.mark.parametrize(
    (
        "canonical_p95_ms",
        "consolidator_100_p95_ms",
        "expected_canonical_gate",
        "expected_consolidator_gate",
    ),
    [
        (PHASE2A_P95_MATERIAL_LIMIT_MS, 10.000001, True, False),
        (PHASE2A_P95_MATERIAL_LIMIT_MS + 0.000001, 10.0, False, True),
        (PHASE2A_P95_MATERIAL_LIMIT_MS + 0.000001, 10.000001, False, False),
    ],
)
def test_performance_policy_separates_advisory_and_strict_enforcement(
    canonical_p95_ms: float,
    consolidator_100_p95_ms: float,
    expected_canonical_gate: bool,
    expected_consolidator_gate: bool,
) -> None:
    inputs = {
        "canonical_p95_ms": canonical_p95_ms,
        "consolidator_100_p95_ms": consolidator_100_p95_ms,
        "retained_count_500": 256,
        "retained_count_1000": 256,
        "network_call_count": 0,
    }

    advisory = evaluate_performance_policy(**inputs, enforcement_mode="advisory")
    strict = evaluate_performance_policy(**inputs, enforcement_mode="strict")

    assert advisory["wallClockGates"] == {
        "consolidator_100_p95_at_most_10_ms": expected_consolidator_gate,
        "canonical_p95_not_materially_above_phase2a": expected_canonical_gate,
    }
    assert advisory["deterministicPassed"] is True
    assert advisory["strictPassed"] is False
    assert advisory["enforcementPassed"] is True
    assert phase2b_exit_code(
        {"acceptance_passed": True, "performance": advisory}
    ) == 0
    assert strict["deterministicPassed"] is True
    assert strict["strictPassed"] is False
    assert strict["enforcementPassed"] is False
    assert phase2b_exit_code({"acceptance_passed": True, "performance": strict}) == 1


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("canonical_p95_ms", True),
        ("canonical_p95_ms", float("nan")),
        ("consolidator_100_p95_ms", float("inf")),
        ("consolidator_100_p95_ms", -0.001),
        ("retained_count_500", 255.5),
        ("retained_count_1000", -1),
        ("network_call_count", False),
        ("network_call_count", -1),
        ("enforcement_mode", None),
        ("enforcement_mode", "automatic"),
        ("enforcement_mode", []),
    ],
)
def test_performance_policy_rejects_invalid_measurements(
    field: str,
    invalid: object,
) -> None:
    inputs: dict[str, object] = {
        "canonical_p95_ms": 2.5,
        "consolidator_100_p95_ms": 5.0,
        "retained_count_500": 256,
        "retained_count_1000": 256,
        "network_call_count": 0,
        "enforcement_mode": "advisory",
    }
    inputs[field] = invalid

    with pytest.raises(ValueError, match=field):
        evaluate_performance_policy(**inputs)


def test_performance_policy_keeps_deterministic_gates_mandatory() -> None:
    passing = {
        "canonical_p95_ms": PHASE2A_P95_MATERIAL_LIMIT_MS,
        "consolidator_100_p95_ms": 10.0,
        "retained_count_500": 256,
        "retained_count_1000": 256,
        "network_call_count": 0,
    }
    exact_limit = evaluate_performance_policy(
        **passing,
        enforcement_mode="strict",
    )
    assert exact_limit["wallClockGates"] == {
        "consolidator_100_p95_at_most_10_ms": True,
        "canonical_p95_not_materially_above_phase2a": True,
    }
    assert exact_limit["strictPassed"] is True
    assert exact_limit["enforcementPassed"] is True
    assert phase2b_exit_code(
        {"acceptance_passed": True, "performance": exact_limit}
    ) == 0

    for invalid_deterministic_input in (
        {"network_call_count": 1},
        {"retained_count_500": 257},
        {"retained_count_1000": 257},
    ):
        inputs = {**passing, **invalid_deterministic_input}
        advisory = evaluate_performance_policy(
            **inputs,
            enforcement_mode="advisory",
        )
        strict = evaluate_performance_policy(
            **inputs,
            enforcement_mode="strict",
        )
        assert advisory["deterministicPassed"] is False
        assert advisory["enforcementPassed"] is False
        assert strict["deterministicPassed"] is False
        assert strict["enforcementPassed"] is False
        assert phase2b_exit_code(
            {"acceptance_passed": True, "performance": advisory}
        ) == 1
        assert phase2b_exit_code(
            {"acceptance_passed": True, "performance": strict}
        ) == 1


def test_phase2b_exit_code_fails_closed_and_enforces_strict_mode() -> None:
    advisory = {
        "acceptance_passed": True,
        "performance": {
            "enforcementMode": "advisory",
            "deterministicPassed": True,
            "strictPassed": False,
        },
    }
    strict_failure = {
        **advisory,
        "performance": {**advisory["performance"], "enforcementMode": "strict"},
    }
    strict_success = {
        **strict_failure,
        "performance": {**strict_failure["performance"], "strictPassed": True},
    }
    missing_strict_result = {
        **strict_failure,
        "performance": {
            key: value
            for key, value in strict_failure["performance"].items()
            if key != "strictPassed"
        },
    }

    assert phase2b_exit_code(advisory) == 0
    assert phase2b_exit_code(strict_failure) == 1
    assert phase2b_exit_code(strict_success) == 0
    assert phase2b_exit_code(missing_strict_result) == 1
    assert phase2b_exit_code({**advisory, "acceptance_passed": False}) == 1
    assert phase2b_exit_code(
        {
            **advisory,
            "performance": {
                **advisory["performance"],
                "deterministicPassed": False,
            },
        }
    ) == 1


@lru_cache(maxsize=1)
def _report() -> dict:
    with tempfile.TemporaryDirectory(prefix="phase2b-test-formal-") as temporary:
        return evaluate_phase2b(
            project_root=ROOT,
            holdout_path=HOLDOUT,
            phase1_dataset_root=PHASE1_DATASET,
            phase2a_baseline_path=PHASE2A_BASELINE,
            formal_root=Path(temporary) / ".mini-code",
        )


def test_default_report_uses_deterministic_advisory_acceptance() -> None:
    report = _report()
    performance = report["performance"]

    assert report["schema_version"] == "memory-retrieval-phase2b-v1"
    assert report["evaluator_version"] == "1.1.0"
    assert performance["enforcementMode"] == "advisory"
    assert performance["strictPassed"] is all(
        performance["wallClockGates"].values()
    )
    assert all(performance["deterministicGates"].values())
    assert (
        performance["observations"]["canonicalLatencyMs"]["p95_ms"]
        == performance["current_canonical_p95_ms"]
    )
    assert (
        performance["observations"]["consolidator"]
        == performance["consolidator"]
    )
    assert performance["observations"]["holdoutPeakMemoryBytes"] > 0
    assert report["acceptance_passed"] is (
        all(report["phase2a_80_case"]["gates"].values())
        and all(report["holdout"]["gates"].values())
        and all(performance["deterministicGates"].values())
        and all(report["integrity"]["gates"].values())
    )


def test_holdout_has_33_cases_and_all_required_categories() -> None:
    document = json.loads(HOLDOUT.read_text(encoding="utf-8"))

    assert len(document["cases"]) == 33
    assert len({case["case_id"] for case in document["cases"]}) == 33
    assert len({case["category"] for case in document["cases"]}) == 11
    assert len(
        {memory["id"] for case in document["cases"] for memory in case["memories"]}
    ) == 69


def test_holdout_and_artifact_validate_against_json_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(ARTIFACT_SCHEMA.read_text(encoding="utf-8"))

    jsonschema.validate(
        json.loads(HOLDOUT.read_text(encoding="utf-8")),
        json.loads(HOLDOUT_SCHEMA.read_text(encoding="utf-8")),
    )
    jsonschema.validate(_report(), schema)
    assert {
        "enforcementMode",
        "strictPassed",
        "deterministicPassed",
        "enforcementPassed",
        "deterministicGates",
        "wallClockGates",
        "observations",
    } <= set(schema["properties"]["performance"]["required"])


def test_all_phase2b_acceptance_gate_groups_pass() -> None:
    report = _report()

    assert report["acceptance_passed"] is True
    assert all(report["phase2a_80_case"]["gates"].values())
    assert all(report["holdout"]["gates"].values())
    assert all(report["performance"]["deterministicGates"].values())
    assert report["performance"]["strictPassed"] is all(
        report["performance"]["wallClockGates"].values()
    )
    assert all(report["integrity"]["gates"].values())


def test_holdout_metrics_distinguish_retrieval_consolidation_and_budget() -> None:
    metrics = _report()["holdout"]["metrics"]

    assert metrics["retrieval_candidate_recall"] == 1.0
    assert metrics["post_gate_recall"] == 1.0
    assert metrics["post_consolidation_precision"] == 1.0
    assert metrics["post_consolidation_recall"] == 1.0
    assert metrics["rendered_recall"] < metrics["post_consolidation_recall"]
    assert metrics["incorrect_suppression_rate"] == 0.0
    assert metrics["reason_code_accuracy"] == 1.0


def test_frozen_80_case_quality_and_remaining_violations_are_explicit() -> None:
    phase2a = _report()["phase2a_80_case"]

    assert phase2a["metrics"]["rendered_precision"] >= 0.95
    assert phase2a["metrics"]["recall_at_5"] >= 0.95
    assert phase2a["metrics"]["primary_hit_rate"] >= 0.985
    assert phase2a["remaining_must_exclude_violations"] == [
        {"case_id": "mr-domain-06", "entry_ids": ["mr-domain-06-noise"]},
        {"case_id": "mr-domain-07", "entry_ids": ["mr-domain-07-noise"]},
        {"case_id": "mr-recovery-06", "entry_ids": ["mr-recovery-06-unverified"]},
    ]


def test_report_contains_no_memory_content_or_raw_provenance() -> None:
    serialized = json.dumps(_report(), ensure_ascii=False).lower()

    assert "catalog lookup uses normalized sku" not in serialized
    assert "checkout cache isolation keys by account id" not in serialized
    assert "widget mode must be enabled" not in serialized
    assert '"provenance"' not in serialized
    assert "authorization: bearer" not in serialized
    assert "secret=" not in serialized


def test_network_io_determinism_and_caps_are_reported() -> None:
    report = _report()
    performance = report["performance"]
    consolidator_p95_ms = performance["consolidator"]["100"]["p95_ms"]
    canonical_p95_ms = performance["current_canonical_p95_ms"]

    assert report["remote_call_count"] == 0
    assert report["determinism"]["two_holdout_runs_equal_without_latency"] is True
    assert (
        isinstance(consolidator_p95_ms, (int, float))
        and not isinstance(consolidator_p95_ms, bool)
        and math.isfinite(consolidator_p95_ms)
        and consolidator_p95_ms >= 0
    )
    assert (
        performance["observations"]["consolidator"]["100"]["p95_ms"]
        == consolidator_p95_ms
    )
    assert performance["wallClockGates"][
        "consolidator_100_p95_at_most_10_ms"
    ] is (consolidator_p95_ms <= 10.0)
    assert performance["wallClockGates"][
        "canonical_p95_not_materially_above_phase2a"
    ] is (canonical_p95_ms <= PHASE2A_P95_MATERIAL_LIMIT_MS)
    assert performance["strictPassed"] is all(performance["wallClockGates"].values())
    assert phase2b_exit_code(report) == 0
    strict_report = copy.deepcopy(report)
    strict_report["performance"]["enforcementMode"] = "strict"
    assert phase2b_exit_code(strict_report) == (
        0 if performance["strictPassed"] else 1
    )
    assert performance["consolidator"]["500"]["retained_count"] <= 256
    assert performance["consolidator"]["1000"]["retained_count"] <= 256
    assert all(performance["deterministicGates"].values())
    assert (
        performance["complexity"]
        == "O(N log N + P + B^2), with deterministic buckets and B<=256"
    )
    assert report["io"]["suppressed_candidates_add_saves"] is False


def test_frozen_assets_match_authoritative_start_hashes() -> None:
    integrity = _report()["integrity"]

    assert len(PHASE1_FROZEN_HASHES) == 15
    assert len(PHASE2A_FROZEN_HASHES) == 8
    assert integrity["phase1_frozen_before"]["matches"] is True
    assert integrity["phase1_frozen_after"]["matches"] is True
    assert integrity["phase2a_frozen_before"]["matches"] is True
    assert integrity["phase2a_frozen_after"]["matches"] is True


def test_deterministic_view_excludes_environment_sensitive_measurements() -> None:
    report = _report()
    changed_observations = copy.deepcopy(report)
    changed_observations["performance"]["enforcementMode"] = "strict"
    changed_observations["performance"]["strictPassed"] = not report["performance"][
        "strictPassed"
    ]
    changed_observations["performance"]["observations"]["canonicalLatencyMs"][
        "p95_ms"
    ] = 999.0
    changed_observations["holdout"]["peak_memory_bytes"] += 1
    for result in changed_observations["holdout"]["cases"]:
        result["latency_ms"] += 1.0
    changed_observations["phase2a_80_case"]["latency"]["p95_ms"] += 1.0

    view = deterministic_phase2b_view(report)

    assert "performance" not in view
    assert "peak_memory_bytes" not in view["holdout"]
    assert all("latency_ms" not in case for case in view["holdout"]["cases"])
    assert "latency" not in view["phase2a_80_case"]
    assert view == deterministic_phase2b_view(changed_observations)


def test_cli_writes_parseable_reports_and_repeatable_core(tmp_path: Path) -> None:
    cores = []
    for run in (1, 2):
        output = tmp_path / f"phase2b-{run}.json"
        markdown = tmp_path / f"phase2b-{run}.md"
        comparison = tmp_path / f"comparison-{run}.md"
        performance = tmp_path / f"performance-{run}.md"
        core = tmp_path / f"core-{run}.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "evaluate_memory_retrieval_phase2b.py"),
                "--output",
                str(output),
                "--markdown",
                str(markdown),
                "--comparison",
                str(comparison),
                "--performance",
                str(performance),
                "--deterministic-core-output",
                str(core),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["acceptance_passed"] is True
        assert report["performance"]["enforcementMode"] == "advisory"
        accuracy_text = markdown.read_text(encoding="utf-8")
        performance_text = performance.read_text(encoding="utf-8")
        assert accuracy_text.startswith("# Memory Retrieval Phase 2B")
        assert "Deterministic acceptance" in accuracy_text
        assert "- Enforcement mode: `advisory`." in performance_text
        assert "- Strict wall-clock result: `" in performance_text
        assert "- Material limit: `" in performance_text
        assert comparison.is_file() and performance.is_file()
        cores.append(core.read_bytes())

    assert cores[0] == cores[1]


def test_cli_requires_explicit_strict_flag_and_rejects_unknown_arguments() -> None:
    from scripts.evaluate_memory_retrieval_phase2b import parse_args

    assert parse_args([]).enforce_wall_clock_performance is False
    assert (
        parse_args(["--enforce-wall-clock-performance"]).enforce_wall_clock_performance
        is True
    )
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--unknown-phase2b-option"])
    assert exc_info.value.code == 2
