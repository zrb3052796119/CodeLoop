"""Prospective common generated-test scoring; synthetic evidence only."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("numpy")
from scripts import analyze_implementation_progress as analysis


def experiment():
    spec = importlib.util.spec_from_file_location(
        "_direct_workflow_fixtures",
        Path(__file__).with_name("test_implementation_progress_analysis.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest, results = module.fixture()
    manifest.update(schemaVersion=5, protocolVersion=analysis.REGRESSION_PROTOCOL)
    for row in results["trials"]:
        row.update(
            oracleSuccess=True,
            success=True,
            regressionTestPassed=True,
            regressionTest={
                "passed": True,
                "status": "passed",
                "durationSeconds": 0.1,
                "counts": {
                    "collected": 2,
                    "passed": 2,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                    "xfailed": 0,
                },
            },
        )
    return manifest, results


@pytest.mark.parametrize("arm", analysis.ARMS)
def test_hidden_pass_and_generated_failure_is_unsuccessful_in_every_arm(arm):
    manifest, results = experiment()
    row = next(row for row in results["trials"] if row["arm"] == arm)
    row.update(success=False, regressionTestPassed=False)
    row["regressionTest"].update(passed=False, status="failed")
    row["regressionTest"]["counts"].update(passed=1, failed=1)
    summary = analysis.analyze(manifest, results)
    assert summary["arms"][arm]["oracleSuccesses"] == 16
    assert summary["arms"][arm]["regressionTestsPassed"] == 15
    assert summary["arms"][arm]["successes"] == 15
    assert summary["arms"][arm]["trials"] == 16
    row["success"] = True
    with pytest.raises(analysis.AnalysisError, match="Success does not match"):
        analysis.validate(manifest, results)


@pytest.mark.parametrize("arm", analysis.ARMS)
@pytest.mark.parametrize("failed_field", ("oracleSuccess", "scopePassed"))
def test_generated_pass_cannot_replace_hidden_or_scope_success(arm, failed_field):
    manifest, results = experiment()
    row = next(row for row in results["trials"] if row["arm"] == arm)
    row.update({failed_field: False, "success": False})
    analysis.validate(manifest, results)
    row["success"] = True
    with pytest.raises(analysis.AnalysisError, match="Success does not match"):
        analysis.validate(manifest, results)


@pytest.mark.parametrize("count", ("failed", "errors", "skipped", "xfailed", "xpassed"))
def test_regression_success_cannot_hide_nonpassing_outcomes(count):
    manifest, results = experiment()
    results["trials"][0]["regressionTest"]["counts"][count] = 1
    with pytest.raises(analysis.AnalysisError, match="all-passed"):
        analysis.validate(manifest, results)


@pytest.mark.parametrize("mutation", ("zero", "missing", "flag", "not_boolean"))
def test_missing_or_empty_judge_evidence_cannot_claim_pass(mutation):
    manifest, results = experiment()
    row = results["trials"][0]
    if mutation == "zero":
        row["regressionTest"]["counts"].update(collected=0, passed=0)
    elif mutation == "missing":
        row.pop("regressionTestPassed")
    elif mutation == "flag":
        row["regressionTest"]["passed"] = False
    else:
        row["regressionTestPassed"] = 1
    with pytest.raises(analysis.AnalysisError):
        analysis.validate(manifest, results)


@pytest.mark.parametrize("schema", (3, 4))
def test_legacy_schema_keeps_old_success_formula_without_new_test_fields(schema):
    manifest, results = experiment()
    manifest.update(schemaVersion=schema, protocolVersion="direct-workflow-v1")
    for row in results["trials"]:
        row.pop("regressionTestPassed")
        row.pop("regressionTest")
    summary = analysis.analyze(manifest, results)
    assert all(arm["successes"] == 16 for arm in summary["arms"].values())
    assert all(arm["regressionTestsPassed"] is None for arm in summary["arms"].values())
    assert "新增回归测试通过" not in analysis.report(summary)


@pytest.mark.parametrize("schema", (None, 3, 4, 6))
def test_new_protocol_requires_schema_five(schema):
    manifest, results = experiment()
    manifest["schemaVersion"] = schema
    with pytest.raises(analysis.AnalysisError, match="Only the formal"):
        analysis.validate(manifest, results)


def test_common_judge_does_not_add_statistical_families_or_change_pairing():
    manifest, results = experiment()
    old_manifest = {**manifest, "schemaVersion": 4, "protocolVersion": "direct-workflow-v1"}
    old_results = deepcopy(results)
    for row in old_results["trials"]:
        row.pop("regressionTestPassed")
        row.pop("regressionTest")
    expected = analysis.analyze(old_manifest, old_results)
    actual = analysis.analyze(manifest, results)
    assert actual["comparisons"] == expected["comparisons"]
    assert actual["tasks"] == expected["tasks"]
    assert actual["design"]["trials"] == 48
    assert actual["design"]["bootstrapResamples"] == 20000
    assert actual["design"]["exactSignFlips"] == 256
    assert len([c for c in actual["comparisons"] if c["family"] == "primary"]) == 3
    assert len([c for c in actual["comparisons"] if c["family"] == "secondary"]) == 6
    report = analysis.report(actual)
    assert "| 新增回归测试通过 | 16/16 | 16/16 | 16/16 |" in report
    assert "至少收集 1 项测试" in report and "全部真实 passed" in report
    assert "上限 120 秒" in report and "不计入 600 秒" in report
    assert "复制、两类验收、回执与进程清理共用总上限" in report
    assert "原独立 oracle、新增回归测试均通过" in report


def test_regression_scoring_does_not_fill_missing_usage_or_cost():
    manifest, results = experiment()
    row = next(row for row in results["trials"] if row["arm"] == "auto")
    row.update(
        usageComplete=False,
        tokenUsageComplete=False,
        cacheUsageComplete=False,
        estimatedCostCny=None,
        unknownUsageCalls=1,
        knownCostLowerBoundCny=0.00001,
    )
    summary = analysis.analyze(manifest, results)
    assert summary["arms"]["auto"]["regressionTestsPassed"] == 16
    assert summary["arms"]["auto"]["costCny"] is None
    comparison = next(c for c in summary["comparisons"] if c["id"] == "auto-single/estimatedCostCny")
    assert not comparison["available"] and comparison["holmP"] is None
