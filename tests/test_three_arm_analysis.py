"""Synthetic evidence only; never runs models or alters experiment snapshots."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy", reason="Optional analysis dependency")
analysis = importlib.import_module("scripts.analyze_three_arm_real")


def experiment(tasks=8, repeats=2):
    schedule, rows = [], []
    for i in range(tasks):
        for repeat in range(1, repeats + 1):
            for j, arm in enumerate(analysis.ARMS):
                assignment = {
                    "id": f"synthetic-{i}-{repeat}-{arm}",
                    "case": f"synthetic-{i}",
                    "repeat": repeat,
                    "arm": arm,
                }
                schedule.append(assignment)
                explicit = arm == "explicit"
                rows.append(
                    {
                        **assignment,
                        "category": "synthetic",
                        "success": explicit,
                        "oracleSuccess": explicit,
                        "scopePassed": True,
                        "collaborationExecuted": explicit,
                        "collaborationProtocolSatisfied": explicit,
                        "modelCalls": 4 if explicit else 2,
                        "leafModelCalls": 2 if explicit else 0,
                        "successfulLeafRuns": 2 if explicit else 0,
                        "deliveredLeafResults": 2 if explicit else 0,
                        "actualLeafRuns": 2 if explicit else 0,
                        "readAnalysisLeafRuns": 1 if explicit else 0,
                        "implementationLeafRuns": 1 if explicit else 0,
                        "delegationAttempts": 2 if explicit else 0,
                        "durationSeconds": 10 + i + j,
                        "inputTokens": 100,
                        "outputTokens": 20,
                        "cacheReadTokens": 0,
                        "totalTokens": 120,
                        "usageComplete": True,
                        "tokenUsageComplete": True,
                        "cacheUsageComplete": True,
                        "unknownUsageCalls": 0,
                        "estimatedCostCny": 0.001 * (j + 1),
                        "knownCostLowerBoundCny": 0.001 * (j + 1),
                        "errorClass": None,
                    }
                )
    return {"schedule": schedule, "repeats": repeats, "model": "synthetic-only"}, {
        "expectedTrials": len(schedule),
        "trials": rows,
    }


def test_primary_three_and_secondary_six_families_remain_separate():
    manifest, results = experiment()
    summary = analysis.analyze(manifest, results)
    primary = [c for c in summary["comparisons"] if c["family"] == "primary"]
    secondary = [c for c in summary["comparisons"] if c["family"] == "secondary"]
    assert len(primary) == 3 and len(secondary) == 6
    assert all(c["exactP"] == 2 / 256 for c in primary)
    assert all(c["holmP"] == 3 * 2 / 256 for c in primary)
    assert (
        next(c for c in secondary if c["id"] == "explicit-auto/success")["holmP"]
        == 6 * 2 / 256
    )
    assert summary["design"]["tasks"] == 8
    assert summary["design"]["trials"] == 48
    assert summary["arms"]["explicit"]["successes"] == 16
    assert summary["mechanismGate"]["passed"] is True


def test_schedule_drives_task_count_and_repeats_instead_of_old_hardcoded_design():
    manifest, results = experiment(tasks=6, repeats=3)
    summary = analysis.analyze(manifest, results)
    assert summary["design"]["trials"] == 54
    assert summary["design"]["exactSignFlips"] == 64
    assert summary["arms"]["explicit"]["trials"] == 18
    assert summary == analysis.analyze(manifest, results)


def test_protocol_failures_stay_in_denominator_and_block_mechanism_claims():
    manifest, results = experiment()
    explicit = [r for r in results["trials"] if r["arm"] == "explicit"]
    for row in explicit[:4]:
        row.update(
            success=False,
            collaborationProtocolSatisfied=False,
            errorClass="ProtocolFailure",
        )
    summary = analysis.analyze(manifest, results)
    assert summary["arms"]["explicit"]["oracleSuccesses"] == 16
    assert summary["arms"]["explicit"]["successes"] == 12
    assert summary["arms"]["explicit"]["trials"] == 16
    assert summary["mechanismGate"]["observedCoverage"] == 0.75
    assert summary["mechanismGate"]["passed"] is False
    report = analysis.report(summary)
    assert report.index("机制未充分触发") < report.index("| 指标 |")
    assert "次要发现不能替代主问题" in report
    assert "auto 未实际执行子任务" in report


def test_cost_missingness_blocks_only_affected_pairs_and_never_zero_fills():
    manifest, results = experiment()
    row = next(r for r in results["trials"] if r["arm"] == "auto")
    row.update(
        usageComplete=False,
        tokenUsageComplete=False,
        cacheUsageComplete=False,
        unknownUsageCalls=1,
        estimatedCostCny=None,
        knownCostLowerBoundCny=0.0001,
    )
    summary = analysis.analyze(manifest, results)
    costs = {
        c["id"]: c for c in summary["comparisons"] if c["metric"] == "estimatedCostCny"
    }
    assert costs["explicit-single/estimatedCostCny"]["available"] is True
    assert costs["auto-single/estimatedCostCny"]["available"] is False
    assert costs["explicit-auto/estimatedCostCny"]["holmP"] is None
    assert summary["arms"]["auto"]["knownCostLowerBoundCny"] == pytest.approx(
        0.002 * 15 + 0.0001
    )
    assert summary["arms"]["auto"]["tokenSemantics"] == "known_lower_bound"
    assert summary["arms"]["auto"]["unknownUsageCalls"] == 1
    report = analysis.report(summary)
    assert "| 缺输入/输出用量请求 / 全部请求 | 0/32 | 1/32 | 0/64 |" in report
    assert "| 已知输入 token | 1600 | ≥1600 | 1600 |" in report
    assert "| 已知输出 token | 320 | ≥320 | 320 |" in report
    assert "费用所需用量缺失" in report


def test_cache_only_missingness_preserves_exact_input_output_counts():
    manifest, results = experiment()
    row = next(r for r in results["trials"] if r["arm"] == "auto")
    row.update(
        usageComplete=False,
        cacheUsageComplete=False,
        estimatedCostCny=None,
        knownCostLowerBoundCny=0.0001,
    )
    summary = analysis.analyze(manifest, results)
    arm = summary["arms"]["auto"]
    assert arm["tokenUsageComplete"] is True
    assert arm["tokenSemantics"] == "complete"
    assert arm["cacheTokenSemantics"] == "known_lower_bound"
    assert arm["unknownUsageCalls"] == 0
    assert arm["tokenUsageCompleteTrials"] == 16
    assert arm["cacheUsageCompleteTrials"] == 15
    assert arm["usageCompleteTrials"] == 15
    assert arm["costCny"] is None
    report = analysis.report(summary)
    assert "| 已知输入 token | 1600 | 1600 | 1600 |" in report
    assert "| 已知输出 token | 320 | 320 | 320 |" in report
    assert "| 缓存拆分完整 trial | 16/16 | 15/16 | 16/16 |" in report
    assert "| 缺输入/输出用量请求 / 全部请求 | 0/32 | 0/32 | 0/64 |" in report


def test_complete_paired_price_interval_uses_average_trial_cny_and_known_cache():
    manifest, results = experiment()
    manifest["price"] = {
        "perMillionInput": 0.8,
        "perMillionOutput": 2.8,
        "perMillionCacheRead": 0.23,
    }
    # Each explicit trial adds 1,000 uncached input tokens: 0.0008 CNY.
    for row in results["trials"]:
        row["inputTokens"] = 2000 if row["arm"] == "explicit" else 1000
        row["cacheReadTokens"] = 400
        row["totalTokens"] = row["inputTokens"] + row["outputTokens"]
        row["estimatedCostCny"] = (
            (row["inputTokens"] - 400) * 0.8 + 400 * 0.23 + 20 * 2.8
        ) / 1e6
        row["knownCostLowerBoundCny"] = row["estimatedCostCny"]
    # An incomplete third arm must not block the valid primary pair's interval.
    auto = next(row for row in results["trials"] if row["arm"] == "auto")
    auto.update(usageComplete=False, cacheUsageComplete=False, estimatedCostCny=None)
    summary = analysis.analyze(manifest, results)
    cost = next(
        c
        for c in summary["comparisons"]
        if c["id"] == "explicit-single/estimatedCostCny"
    )
    assert cost["delta"] == pytest.approx(0.0008)
    assert cost["delta95CI"] == pytest.approx([0.0008, 0.0008])
    assert cost["ratio"] == pytest.approx(0.001428 / 0.000628)
    assert cost["ratio95CI"] == pytest.approx([cost["ratio"], cost["ratio"]])
    results["trials"][0]["estimatedCostCny"] += 0.0001
    with pytest.raises(analysis.AnalysisError, match="frozen price"):
        analysis.analyze(manifest, results)


@pytest.mark.parametrize(
    "patch",
    [
        {"usageComplete": False, "estimatedCostCny": None},
        {"unknownUsageCalls": 5},
        {"unknownUsageCalls": 1},
        {"tokenUsageComplete": False},
    ],
)
def test_inconsistent_completeness_or_request_denominator_rejected(patch):
    manifest, results = experiment()
    results["trials"][0].update(patch)
    with pytest.raises(analysis.AnalysisError):
        analysis.analyze(manifest, results)


def test_implementation_failure_does_not_erase_actual_collaboration_coverage():
    manifest, results = experiment()
    for row in results["trials"]:
        if row["arm"] == "explicit":
            row.update(
                success=False,
                oracleSuccess=False,
                successfulLeafRuns=0,
                errorClass="VerificationFailure",
            )
    summary = analysis.analyze(manifest, results)
    assert summary["arms"]["explicit"]["successes"] == 0
    assert summary["arms"]["explicit"]["successfulLeafRuns"] == 0
    assert summary["mechanismGate"]["observedCoverage"] == 1
    assert summary["mechanismGate"]["passed"] is True


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "duplicate",
        "no-leaf",
        "single-delegation",
        "oracle-success-mismatch",
        "leaf-count",
        "complete-cost",
    ],
)
def test_invalid_or_incomplete_evidence_is_rejected(tamper):
    manifest, results = experiment()
    row = next(r for r in results["trials"] if r["arm"] == "explicit")
    if tamper == "missing":
        results["trials"].pop()
    elif tamper == "duplicate":
        results["trials"][-1] = copy.deepcopy(results["trials"][0])
    elif tamper == "no-leaf":
        row["leafModelCalls"] = 0
    elif tamper == "single-delegation":
        results["trials"][0]["delegationAttempts"] = 1
    elif tamper == "oracle-success-mismatch":
        row["oracleSuccess"] = False
    elif tamper == "leaf-count":
        row["implementationLeafRuns"] = 0
    else:
        row["usageComplete"] = False
    with pytest.raises(analysis.AnalysisError):
        analysis.analyze(manifest, results)


def test_cli_renders_only_synthetic_temp_data_and_checks_manifest_hash(tmp_path):
    manifest, results = experiment()
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))
    results["manifestSha256"] = analysis.sha(mp)
    rp = tmp_path / "results.json"
    rp.write_text(json.dumps(results))
    out = tmp_path / "synthetic-analysis"
    command = [
        sys.executable,
        str(Path(analysis.__file__).resolve()),
        "--input",
        str(tmp_path),
        "--output",
        str(out),
    ]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert process.returncode == 0, process.stderr
    for file in (
        "analysis-report.md",
        "stats-appendix.md",
        "summary.json",
        "task-summary.csv",
        "figure-catalog.md",
    ):
        assert (out / file).stat().st_size > 50
    assert (
        (out / "figures/three-arm-comparison.png").read_bytes().startswith(b"\x89PNG")
    )
    assert (out / "figures/three-arm-comparison.pdf").read_bytes().startswith(b"%PDF")
    assert (
        (out / "figures/three-arm-coverage-and-calls.png")
        .read_bytes()
        .startswith(b"\x89PNG")
    )
    assert (
        (out / "figures/three-arm-coverage-and-calls.pdf")
        .read_bytes()
        .startswith(b"%PDF")
    )
    results["manifestSha256"] = "wrong"
    rp.write_text(json.dumps(results))
    process = subprocess.run(
        command + ["--validate-only"], capture_output=True, text=True, check=False
    )
    assert process.returncode == 2
    assert "hash mismatch" in process.stderr
