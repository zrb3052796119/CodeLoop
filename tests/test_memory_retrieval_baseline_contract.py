from __future__ import annotations

import json
import socket
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from scripts.memory_retrieval_evaluator import (
    ARMS,
    PRODUCTION_FILES,
    deterministic_report_view,
    evaluate_dataset,
    hash_production_files,
    render_markdown_report,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests" / "fixtures" / "memory_retrieval_golden"


@lru_cache(maxsize=1)
def _report() -> dict:
    return evaluate_dataset(DATASET, project_root=ROOT)


def test_report_contains_required_machine_readable_sections() -> None:
    report = _report()
    required = {
        "schema_version",
        "synthetic_data",
        "dataset_case_count",
        "category_counts",
        "production_file_hashes_before",
        "production_file_hashes_after",
        "arm_configuration",
        "overall_metrics",
        "per_category_metrics",
        "per_case_results",
        "entrypoint_consistency",
        "known_risk_reproductions",
        "latency",
        "token_usage",
        "save_io",
        "unavailable_metrics",
        "limitations",
        "remote_call_count",
        "formal_memory_touched",
    }

    assert required <= set(report)
    assert report["dataset_case_count"] == 80
    assert report["synthetic_data"] is True


def test_each_arm_has_overall_and_per_category_metrics() -> None:
    report = _report()

    assert set(report["overall_metrics"]) == set(ARMS)
    for category_metrics in report["per_category_metrics"].values():
        assert set(category_metrics) == set(ARMS)
    for arm in ARMS:
        assert "precision_at_1" in report["overall_metrics"][arm]
        assert "ndcg_at_5" in report["overall_metrics"][arm]


def test_per_case_pipeline_inject_preserves_four_id_views() -> None:
    report = _report()
    result = report["per_case_results"][0]["arms"]["pipeline_inject"]

    assert isinstance(result["returned_ids"], list)
    assert isinstance(result["last_injected_ids"], list)
    assert isinstance(result["rendered_ids"], list)
    assert isinstance(result["recorded_injection_ids"], list)
    assert isinstance(result["feedback_ids"], list)


def test_known_risk_diagnostics_cover_all_twelve_required_scenarios() -> None:
    risk_ids = {item["risk_id"] for item in _report()["known_risk_reproductions"]}

    assert risk_ids == {
        "global-vs-inject-ordering",
        "max-memories-one",
        "format-first-five-attribution",
        "tui-double-injection",
        "local-budget-before-project",
        "missing-current-files-domains",
        "vector-only-fusion",
        "related-graph-semantics",
        "recovered-failure-feedback",
        "reranker-summary-boundary",
        "headless-no-query-unrelated",
        "repeated-query-counters-io",
    }


def test_diagnostic_flags_are_derived_from_recorded_evidence() -> None:
    diagnostics = {
        item["risk_id"]: item for item in _report()["known_risk_reproductions"]
    }
    max_one = diagnostics["max-memories-one"]
    attribution = diagnostics["format-first-five-attribution"]

    assert max_one["confirmed"] == (max_one["returned_count"] > max_one["limit"])
    assert attribution["confirmed"] == (
        set(attribution["recorded_ids"]) != set(attribution["rendered_ids"])
    )


def test_phase2a_disables_reranker_summary_boundary_without_attack_text() -> None:
    diagnostic = next(
        item
        for item in _report()["known_risk_reproductions"]
        if item["risk_id"] == "reranker-summary-boundary"
    )

    assert diagnostic["safe_fake_model_calls"] == 0
    assert diagnostic["summary_marker_rendered"] is False
    dataset_text = "\n".join(path.read_text(encoding="utf-8") for path in DATASET.rglob("*.json"))
    assert "UNTRUSTED_SUMMARY_MARKER" not in dataset_text


def test_unavailable_metrics_are_explicit_nulls() -> None:
    report = _report()
    global_metrics = report["overall_metrics"]["manager_global_search"]

    assert "actual_rendered_precision" in report["unavailable_metrics"]["manager_global_search"]
    assert global_metrics["actual_rendered_precision"] is None
    assert global_metrics["feedback_attribution_precision"] is None


def test_deterministic_report_core_matches_across_two_runs() -> None:
    first = evaluate_dataset(DATASET, project_root=ROOT)
    second = evaluate_dataset(DATASET, project_root=ROOT)

    assert deterministic_report_view(first) == deterministic_report_view(second)


def test_evaluator_does_not_modify_production_files() -> None:
    before = hash_production_files(ROOT)

    evaluate_dataset(DATASET, project_root=ROOT, include_diagnostics=False)

    assert hash_production_files(ROOT) == before
    assert set(before) == {path for path in PRODUCTION_FILES if (ROOT / path).is_file()}


def test_report_proves_fixture_production_and_formal_memory_stability() -> None:
    report = _report()

    assert report["production_files_unchanged"] is True
    assert report["fixtures_unchanged"] is True
    assert report["formal_memory_touched"] is False
    assert report["remote_call_count"] == 0


def test_markdown_report_distinguishes_fact_inference_and_limits() -> None:
    markdown = render_markdown_report(_report())

    assert "Facts:" in markdown
    assert "Inference:" in markdown
    assert "Limits:" in markdown
    assert "not a production-accuracy claim" in markdown


def test_cli_writes_parseable_json_markdown_and_stable_core(tmp_path: Path) -> None:
    output = tmp_path / "baseline.json"
    markdown = tmp_path / "baseline.md"
    core = tmp_path / "core.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "evaluate_memory_retrieval.py"),
        "--dataset",
        str(DATASET),
        "--output",
        str(output),
        "--markdown",
        str(markdown),
        "--deterministic-core-output",
        str(core),
    ]

    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["dataset_case_count"] == 80
    assert json.loads(core.read_text(encoding="utf-8"))["dataset_case_count"] == 80
    assert "Core Metrics" in markdown.read_text(encoding="utf-8")


def test_full_evaluator_does_not_connect_to_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)

    evaluate_dataset(DATASET, project_root=ROOT, include_diagnostics=False)
    assert calls == []


def test_every_json_fixture_and_generated_schema_is_parseable() -> None:
    paths = sorted(DATASET.rglob("*.json"))

    assert len(paths) == 11
    assert all(json.loads(path.read_text(encoding="utf-8")) for path in paths)
