"""Synthetic successor statistics; no model calls or real experiment writes."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("numpy")
from scripts import analyze_implementation_progress as analysis


def fixture():
    spec = importlib.util.spec_from_file_location(
        "_old_analysis_fixtures", Path(__file__).with_name("test_three_arm_analysis.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest, results = module.experiment()
    manifest.update(
        phase="formal",
        protocolVersion="direct-workflow-v1",
        engineeringGate={"passed": True},
        reusedCalibrationCases=["synthetic-0", "synthetic-1", "synthetic-2"],
    )
    for row in results["trials"]:
        explicit = row["arm"] == "explicit"
        row.update(
            actualAnalystLeafRuns=0,
            deliveredAnalystLeafResults=0,
            externalAnalysisLeafRuns=0,
            externalLeafRuns=0,
            readAnalysisLeafRuns=0,
            implementationLeafRuns=int(explicit),
            actualLeafRuns=int(explicit),
            successfulLeafRuns=int(explicit),
            deliveredLeafResults=int(explicit),
            leafModelCalls=int(explicit),
            deliveredWorkflowResults=int(explicit),
        )
    return manifest, results


def test_optional_analyst_preserves_exact_shared_statistics_and_task_units():
    manifest, results = fixture()
    old_results = deepcopy(results)
    for row in old_results["trials"]:
        if row["arm"] == "explicit":
            row.update(
                readAnalysisLeafRuns=1,
                actualLeafRuns=2,
                deliveredLeafResults=2,
                leafModelCalls=2,
            )
    expected = analysis.legacy.analyze(manifest, old_results)
    actual = analysis.analyze(manifest, results)
    assert actual["comparisons"] == expected["comparisons"]
    assert actual["tasks"] == expected["tasks"]
    assert actual["design"]["tasks"] == 8 and actual["design"]["trials"] == 48
    assert actual["design"]["exactSignFlips"] == 256
    assert actual["design"]["bootstrapResamples"] == 20000
    assert actual["arms"]["explicit"]["actualAnalystLeafRuns"] == 0
    assert actual["arms"]["explicit"]["implementationLeafRuns"] == 16
    assert actual["mechanismGate"]["passed"]
    assert analysis.legacy.validate is not analysis.validate


@pytest.mark.parametrize(
    "mutation", ["engineering", "missing_gate", "missing_trial", "only_six"]
)
def test_engineering_or_incomplete_rows_cannot_enter_formal_statistics(mutation):
    manifest, results = fixture()
    if mutation == "engineering":
        manifest["phase"] = "engineering"
    elif mutation == "missing_gate":
        manifest["engineeringGate"] = None
    elif mutation == "missing_trial":
        results["trials"].pop()
    else:
        manifest["schedule"] = manifest["schedule"][:6]
        results["trials"] = results["trials"][:6]
    with pytest.raises(analysis.AnalysisError):
        analysis.analyze(manifest, results)


def test_unknown_cost_never_turns_into_savings_and_holm_families_stay_fixed():
    manifest, results = fixture()
    for row in results["trials"]:
        if row["arm"] == "explicit":
            row.update(
                usageComplete=False,
                tokenUsageComplete=False,
                cacheUsageComplete=False,
                estimatedCostCny=None,
                unknownUsageCalls=1,
                knownCostLowerBoundCny=0.00001,
            )
    summary = analysis.analyze(manifest, results)
    primary = [c for c in summary["comparisons"] if c["family"] == "primary"]
    assert len(primary) == 3
    cost = next(c for c in primary if c["metric"] == "estimatedCostCny")
    assert not cost["available"] and cost["exactP"] is None and cost["holmP"] is None
    success = next(c for c in primary if c["metric"] == "success")
    assert success["exactP"] == 2 / 256 and success["holmP"] == 3 * 2 / 256
    assert summary["arms"]["explicit"]["tokenSemantics"] == "known_lower_bound"
    assert "不能按零填补或计算节省比例" in analysis.report(summary)


def test_protocol_failures_remain_in_denominator_and_coverage_gate():
    manifest, results = fixture()
    explicit = [row for row in results["trials"] if row["arm"] == "explicit"]
    for row in explicit[:4]:
        row.update(
            collaborationProtocolSatisfied=False,
            implementationLeafRuns=0,
            success=False,
        )
    summary = analysis.analyze(manifest, results)
    assert summary["arms"]["explicit"]["trials"] == 16
    assert summary["arms"]["explicit"]["oracleSuccesses"] == 16
    assert summary["mechanismGate"]["observedCoverage"] == 0.75
    assert not summary["mechanismGate"]["passed"]
    assert "阻断协作收益结论" in analysis.report(summary)


def test_false_claim_of_direct_delivery_is_rejected():
    manifest, results = fixture()
    row = next(r for r in results["trials"] if r["arm"] == "explicit")
    row["externalLeafRuns"] = 1
    with pytest.raises(analysis.AnalysisError, match="protocol fields disagree"):
        analysis.analyze(manifest, results)


def test_zero_floor_regression_and_wall_clock_limits_are_stated():
    manifest, results = fixture()
    for row in results["trials"]:
        row["oracleSuccess"] = row["success"] = False
    summary = analysis.analyze(manifest, results)
    text = analysis.report(summary)
    assert "地板效应" in text and "不能证明组别等效" in text
    assert "不是独立泛化集" in text
    assert "耗时较短不等于更早交付成功修复" in text
    assert "Analyst 按生产策略可选" in text
    assert "再派发 workflow 实现叶子" not in text
    success = next(
        c for c in summary["comparisons"] if c["id"] == "explicit-single/success"
    )
    assert success["delta95CI"] == [0, 0] and success["exactP"] == 1
