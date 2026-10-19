"""Real successor finalizer plus deterministic common-judge evidence, no models."""

from __future__ import annotations

from copy import deepcopy
import subprocess

import pytest

from scripts import run_implementation_progress_live as live


def fixture_trial(tmp_path, arm):
    output = tmp_path / "evaluation"
    trial = {"id": f"fixture-r1-{arm}", "case": "fixture", "repeat": 1, "arm": arm}
    root = output / "private" / trial["id"]
    root.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    (workspace / "minicode").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "minicode/a.py").write_text("VALUE = 1\n")
    live.shared.write_json(root / "workspace.json", {"path": str(workspace)})
    live.shared.write_json(root / "baseline-files.json", live.shared.workspace_files(workspace))
    live.shared.write_json(root / "execution.json", {"durationSeconds": 2.25, "errorClass": None})
    live.shared.write_json(output / "tasks.json", {"cases": [{
        "id": "fixture",
        "category": "test",
        "authorized_paths": ["minicode/a.py", "tests/test_eval_regression.py"],
        "oracle_source": "raise SystemExit(0)\n",
    }]})
    (workspace / "minicode/a.py").write_text("VALUE = 2\n")
    (workspace / "tests/test_eval_regression.py").write_text("def test_new(): assert True\n")
    return output, trial, root, workspace


def judge_evidence(*, oracle_passed=True, regression_passed=True):
    return {
        "durationSeconds": 7.0,
        "cleanupComplete": True,
        "oracle": {"passed": oracle_passed, "status": "passed" if oracle_passed else "failed"},
        "regressionTest": {
            "passed": regression_passed,
            "status": "passed" if regression_passed else "failed",
            "durationSeconds": 3.0,
            "cleanup": {"cleanupComplete": True},
            "counts": {
                "collected": 1, "passed": int(regression_passed),
                "failed": int(not regression_passed), "errors": 0, "skipped": 0, "xfailed": 0,
            },
        },
    }


def install_judge(monkeypatch, evidence, clock=None):
    calls = []

    def run(workspace, root, oracle, response, **kwargs):
        calls.append((workspace, root, oracle, response, kwargs))
        if clock is not None:
            clock[0] += evidence["durationSeconds"]
        return deepcopy(evidence)

    monkeypatch.setattr(live, "run_judgement", run)
    return calls


def install_protocol(monkeypatch, module, *, satisfied):
    # Only isolate the already separately tested delivery evaluator; the real
    # old finalizer and successor wrapper must still compose their formulas.
    metrics = live.collaboration_metrics([], [], [])
    if satisfied:
        metrics.update(
            actualLeafRuns=1, leafModelCalls=1, deliveredLeafResults=1,
            implementationLeafRuns=1, deliveredWorkflowResults=1,
            collaborationExecuted=True, collaborationProtocolSatisfied=True,
        )
    monkeypatch.setattr(module, "collaboration_metrics", lambda *args: dict(metrics))


@pytest.mark.parametrize("arm", ("single", "auto", "explicit"))
@pytest.mark.parametrize("regression_passed", (False, True))
def test_actual_finalizer_conjoins_common_tests_for_every_arm_without_global_patch(
    tmp_path, monkeypatch, arm, regression_passed,
):
    output, trial, _, workspace = fixture_trial(tmp_path, arm)
    original_run = subprocess.run
    original_shared_finalizer = live.shared.finalize_trial
    clock = [100.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    calls = install_judge(monkeypatch, judge_evidence(regression_passed=regression_passed), clock)
    module = live.runner(output)
    install_protocol(monkeypatch, module, satisfied=arm == "explicit")
    row = module.finalize_trial(output, trial, process_seconds=4.0)
    assert row["oracleSuccess"] and row["scopePassed"]
    assert row["regressionTestPassed"] is regression_passed
    assert row["success"] is regression_passed
    assert row["changedSourceFiles"] == ["minicode/a.py"]
    assert row["durationSeconds"] == 2.25
    assert row["judgeSeconds"] >= 7.0
    assert len(calls) == 1 and calls[0][0] == workspace
    assert calls[0][4]["total_seconds"] == 120
    assert live.shared.read_json(output / "trials" / (trial["id"] + ".json")) == row
    assert subprocess.run is original_run
    assert module.subprocess.run is original_run
    assert live.shared.finalize_trial is original_shared_finalizer
    assert live.shared.finalize_trial.__globals__["subprocess"].run is original_run


@pytest.mark.parametrize("failed_condition", ("oracle", "scope", "explicit_protocol"))
def test_generated_test_pass_never_replaces_other_required_evidence(
    tmp_path, monkeypatch, failed_condition,
):
    arm = "explicit" if failed_condition == "explicit_protocol" else "single"
    output, trial, _, workspace = fixture_trial(tmp_path, arm)
    install_judge(monkeypatch, judge_evidence(oracle_passed=failed_condition != "oracle"))
    module = live.runner(output)
    if failed_condition == "scope":
        (workspace / "forbidden.py").write_text("outside authority")
    row = module.finalize_trial(output, trial)
    assert row["regressionTestPassed"] and not row["success"]
    field = {"oracle": "oracleSuccess", "scope": "scopePassed", "explicit_protocol": "collaborationProtocolSatisfied"}[failed_condition]
    assert not row[field]


def test_failed_agent_cleanup_skips_judge_and_retains_full_failure_row(tmp_path, monkeypatch):
    output, trial, root, _ = fixture_trial(tmp_path, "single")
    calls = install_judge(monkeypatch, judge_evidence())
    live.shared.write_json(root / "process-cleanup.json", {"cleanupComplete": False})
    row = live.runner(output).finalize_trial(output, trial, process_code=-9)
    assert calls == []
    assert not row["success"] and not row["regressionTestPassed"]
    assert row["oracleErrorClass"] == "ProcessCleanupIncomplete"
    assert row["regressionTest"]["status"] == "not_run_agent_cleanup_or_workspace"
    assert row["regressionTest"]["counts"]["collected"] == 0
