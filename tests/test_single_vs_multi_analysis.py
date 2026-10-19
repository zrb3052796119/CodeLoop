"""Synthetic data only: prove aggregation, missing-cost and pairing semantics."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="Optional experiment-analysis dependency")
analysis = importlib.import_module("scripts.analyze_single_vs_multi")


def synthetic_experiment():
    schedule, trials = [], []
    for task in range(8):
        for repeat in range(1, 4):
            for arm in analysis.ARMS:
                assignment = {
                    "id": f"synthetic-{task}-{repeat}-{arm}",
                    "case": f"synthetic-{task}",
                    "repeat": repeat,
                    "arm": arm,
                }
                schedule.append(assignment)
                trials.append(
                    {
                        **assignment,
                        "category": "synthetic-only",
                        "success": arm == "multi",
                        "durationSeconds": 10 + task + (2 if arm == "multi" else 0),
                        "inputTokens": 100,
                        "outputTokens": 20,
                        "cacheReadTokens": 0,
                        "totalTokens": 120,
                        "estimatedCostCny": 0.001 if arm == "single" else 0.002,
                        "knownCostLowerBoundCny": 0.001 if arm == "single" else 0.002,
                        "usageComplete": True,
                        "modelCalls": 2,
                        "delegationAttempts": int(arm == "multi"),
                        "subagentRuns": int(arm == "multi"),
                        "taskErrorCodes": [],
                        "errorClass": None,
                    }
                )
    return {"schedule": schedule, "repeats": 3, "model": "synthetic-only"}, {
        "expectedTrials": 48,
        "trials": trials,
    }


def test_task_level_sign_flip_and_holm_are_not_48_trial_tests():
    manifest, results = synthetic_experiment()
    summary = analysis.analyze(manifest, results)
    contrast = summary["comparisons"]["success"]
    assert summary["design"]["tasks"] == 8
    assert contrast["nTasks"] == 8
    assert contrast["deltaMultiMinusSingle"] == 1
    assert contrast["delta95CI"] == [1, 1]
    assert contrast["exactSignFlipP"] == 2 / 256
    assert contrast["holmAdjustedP"] == 3 * 2 / 256
    assert summary["arms"]["single"]["successes"] == 0
    assert summary["arms"]["multi"]["successes"] == 24
    assert summary["arms"]["single"]["costCnyPerSuccess"] is None
    assert summary["comparisons"]["durationSeconds"]["deltaMultiMinusSingle"] == 2
    assert summary["comparisons"]["estimatedCostCny"]["ratioMultiOverSingle"] == 2


def test_identical_results_keep_p_one_and_zero_effect_without_equivalence_claim():
    assert analysis.exact_sign_flip([0] * 8) == 1
    assert analysis.holm([0.01, 0.04, 0.20]) == [0.03, 0.08, 0.20]
    assert analysis.holm([0.01, 0.04, None]) == [0.03, 0.08, None]
    assert analysis.exact_sign_flip([1, -1] * 4) == 1


def test_repetitions_are_averaged_inside_task_and_bootstrap_is_reproducible():
    manifest, results = synthetic_experiment()
    rows = [
        r
        for r in results["trials"]
        if r["case"] == "synthetic-0" and r["arm"] == "multi"
    ]
    rows[0]["durationSeconds"] = 30
    summary = analysis.analyze(manifest, results)
    # Task zero: (30 + 12 + 12)/3 - 10 = 8; other seven tasks: +2.
    assert (
        summary["comparisons"]["durationSeconds"]["pairedTaskDifferences"]
        == [8] + [2] * 7
    )
    assert summary["comparisons"]["durationSeconds"]["deltaMultiMinusSingle"] == 2.75
    assert summary == analysis.analyze(manifest, results)


def test_missing_usage_blocks_cost_contrast_preserves_success_denominator_and_lower_bound():
    manifest, results = synthetic_experiment()
    trial = results["trials"][1]
    trial.update(
        usageComplete=False,
        estimatedCostCny=None,
        knownCostLowerBoundCny=0.0002,
        success=False,
        errorClass="HardTimeout",
    )
    summary = analysis.analyze(manifest, results)
    assert summary["arms"]["multi"]["successes"] == 23
    assert summary["arms"]["multi"]["trials"] == 24
    assert summary["arms"]["multi"]["costCny"] is None
    assert summary["arms"]["multi"]["knownCostLowerBoundCny"] == pytest.approx(
        23 * 0.002 + 0.0002
    )
    assert summary["comparisons"]["estimatedCostCny"]["available"] is False
    assert summary["comparisons"]["estimatedCostCny"]["holmAdjustedP"] is None
    assert summary["comparisons"]["success"]["holmAdjustedP"] == 3 * 2 / 256
    assert summary["arms"]["multi"]["tokenTotalsSemantics"] == "known_lower_bound"
    report = analysis.render_report(summary)
    assert "完整成本不可比" in report
    assert report.index("成本不能判优") < report.index("| 指标 |")
    assert "| 已记录输入 token | 2400 | ≥2400 |" in report
    assert "| 已记录输出 token | 480 | ≥480 |" in report


def test_workflow_container_records_do_not_inflate_delegation_or_cost():
    manifest, results = synthetic_experiment()
    for row in results["trials"]:
        if row["arm"] == "multi":
            row["subagentRuns"] = 2  # One workflow container plus one Executor leaf.
    summary = analysis.analyze(manifest, results)
    arm = summary["arms"]["multi"]
    assert arm["knownSubagentRuns"] == 48
    assert arm["trialsWithDelegationAttempt"] == 24
    assert arm["costCny"]["total"] == pytest.approx(0.002 * 24)
    report = analysis.render_report(summary)
    assert "子任务运行记录（含 workflow 容器）" in report
    assert "不能解释为实际子 Agent 数" in report
    assert "已记录实际子 Agent" not in report


def test_zero_delegation_report_blocks_collaboration_benefit_attribution():
    manifest, results = synthetic_experiment()
    for row in results["trials"]:
        row["delegationAttempts"] = 0
        row["subagentRuns"] = 0
    summary = analysis.analyze(manifest, results)
    report = analysis.render_report(summary)
    assert "观察到的组间差异不能归因于多 Agent 协作" in report
    assert "额外工具定义、提示词长度" in report
    assert "不能写成所有 production 自动路由失效" in report
    assert "direct-edit 预算耗尽后强制升级 workflow" in report
    assert report.index("不能证明真正多 Agent 协作有收益") < report.index("| 指标 |")
    assert "终态/验收失败分类" in report
    assert "不是请求级根因统计" in report
    assert "[final-audit.md](final-audit.md)" in report
    assert "[exploratory-mechanisms.json](exploratory-mechanisms.json)" in report


def test_stats_appendix_distinguishes_bootstrap_intervals_from_adjusted_tests(
    tmp_path, monkeypatch
):
    manifest, results = synthetic_experiment()
    summary = analysis.analyze(manifest, results)
    monkeypatch.setattr(analysis, "write_figures", lambda *_: None)
    analysis.write_bundle(summary, tmp_path)
    appendix = (tmp_path / "stats-appendix.md").read_text()
    assert "不是精确符号翻转检验的反演区间" in appendix
    assert "未做 Holm 同时校正" in appendix
    assert "不能仅凭 CI 宣称显著" in appendix
    assert "Holm p < 0.05" in appendix


def test_entirely_missing_cost_is_unknown_and_frozen_price_mismatch_is_rejected():
    manifest, results = synthetic_experiment()
    for row in results["trials"]:
        row.update(usageComplete=False, estimatedCostCny=None)
        row.pop("knownCostLowerBoundCny")
    summary = analysis.analyze(manifest, results)
    assert summary["arms"]["multi"]["knownCostLowerBoundCny"] is None
    assert "缺用量，不可用" in analysis.render_report(summary)
    manifest, results = synthetic_experiment()
    manifest["price"] = {
        "perMillionInput": 0.8,
        "perMillionOutput": 2.8,
        "perMillionCacheRead": 0.23,
    }
    with pytest.raises(analysis.AnalysisError, match="frozen price"):
        analysis.analyze(manifest, results)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "duplicate",
        "assignment",
        "nonfinite",
        "incomplete-cost",
        "single-delegation",
    ],
)
def test_invalid_and_incomplete_evidence_is_rejected(tamper):
    manifest, results = synthetic_experiment()
    if tamper == "missing":
        results["trials"].pop()
    elif tamper == "duplicate":
        results["trials"][-1] = copy.deepcopy(results["trials"][0])
    elif tamper == "assignment":
        results["trials"][0]["repeat"] = 2
    elif tamper == "nonfinite":
        results["trials"][0]["durationSeconds"] = np.nan
    elif tamper == "incomplete-cost":
        results["trials"][0]["usageComplete"] = False
    else:
        results["trials"][0]["delegationAttempts"] = 1
    with pytest.raises(analysis.AnalysisError):
        analysis.analyze(manifest, results)


def test_cli_writes_figures_and_exact_tables_only_to_synthetic_temp_folder(tmp_path):
    manifest, results = synthetic_experiment()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    results["manifestSha256"] = analysis.digest(manifest_path)
    (tmp_path / "results.json").write_text(json.dumps(results))
    output = tmp_path / "synthetic-analysis"
    process = subprocess.run(
        [
            sys.executable,
            str(Path(analysis.__file__).resolve()),
            "--input",
            str(tmp_path),
            "--output",
            str(output),
        ],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    for name in (
        "analysis-report.md",
        "stats-appendix.md",
        "figure-catalog.md",
        "summary.json",
        "task-summary.csv",
    ):
        assert (output / name).stat().st_size > 50
    for name in ("figure-01-paired-comparison", "figure-02-delegation-and-calls"):
        assert (
            (output / "figures" / (name + ".png")).read_bytes().startswith(b"\x89PNG")
        )
        assert (output / "figures" / (name + ".pdf")).read_bytes().startswith(b"%PDF")
    report = (output / "analysis-report.md").read_text()
    assert "0/24" in report and "24/24" in report
    assert "Coding 套餐接口" in report


def test_cli_missing_trial_does_not_create_output(tmp_path):
    manifest, results = synthetic_experiment()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    results["manifestSha256"] = analysis.digest(manifest_path)
    results["trials"].pop()
    (tmp_path / "results.json").write_text(json.dumps(results))
    output = tmp_path / "must-not-exist"
    process = subprocess.run(
        [
            sys.executable,
            str(Path(analysis.__file__).resolve()),
            "--input",
            str(tmp_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert "47/48" in process.stderr
    assert not output.exists()


def test_cli_derives_missing_fallback_category_only_from_frozen_tasks(tmp_path):
    manifest, results = synthetic_experiment()
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": f"synthetic-{i}", "category": "synthetic-only"}
                    for i in range(8)
                ]
            }
        )
    )
    manifest["taskSha256"] = analysis.digest(tasks_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    results["manifestSha256"] = analysis.digest(manifest_path)
    results["trials"][0].pop("category")
    (tmp_path / "results.json").write_text(json.dumps(results))
    process = subprocess.run(
        [
            sys.executable,
            str(Path(analysis.__file__).resolve()),
            "--input",
            str(tmp_path),
            "--validate-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["wroteBundle"] is False
