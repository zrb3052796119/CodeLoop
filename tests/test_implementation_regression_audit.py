"""Independent final-judge audit checks use actual host receipts, not row claims."""

from __future__ import annotations

import json


from scripts import audit_implementation_progress as audit
from scripts import implementation_judge as judge
from tests.test_implementation_final_judge import delivered


def evidence(tmp_path, *, passes=True):
    directory = tmp_path / "experiment"
    private = directory / "private/trial"
    private.mkdir(parents=True)
    workspace, oracle, response = delivered(
        tmp_path, "def test_result():\n    assert " + str(passes) + "\n"
    )
    result = judge.run_judgement(workspace, private / "judgement", oracle, response)
    row = {
        "id": "trial",
        "arm": "single",
        "oracleSuccess": result["oracle"]["passed"],
        "scopePassed": True,
        "collaborationProtocolSatisfied": False,
        "success": passes,
        "regressionTestPassed": result["regressionTest"]["passed"],
        "regressionTest": result["regressionTest"],
        "judgement": result,
        "changedFiles": [],
        "changedSourceFiles": [],
    }
    return directory, row, {"schemaVersion": 5, "judgementPolicy": judge.JUDGE_POLICY}


def test_independent_audit_recomputes_pass_from_phase_receipts(tmp_path):
    directory, row, manifest = evidence(tmp_path)
    examiner = audit.Audit(directory)
    assert examiner.regression_judgement(row, manifest) is True
    assert examiner.issues == []


def test_failed_generated_tests_stay_failed_in_legacy_audit_summary(
    tmp_path, monkeypatch
):
    directory, row, manifest = evidence(tmp_path, passes=False)

    def legacy_trial(self, row, *args):
        self.trials.append(
            {"success": row["success"], "diagnostics": {"terminalOutcomes": []}}
        )

    monkeypatch.setattr(audit.legacy.Audit, "trial", legacy_trial)
    examiner = audit.Audit(directory)
    examiner.trial(row, {"authorized_paths": []}, manifest)
    assert examiner.issues == []
    assert examiner.trials[-1]["success"] is False
    assert examiner.trials[-1]["diagnostics"]["terminalOutcomes"] == [
        "regression_tests_failed"
    ]


def test_row_pass_claim_cannot_override_failed_host_receipt(tmp_path):
    directory, row, manifest = evidence(tmp_path, passes=False)
    row["regressionTestPassed"] = True
    examiner = audit.Audit(directory)
    assert examiner.regression_judgement(row, manifest) is False
    assert any(i["code"] == "regression_pass" for i in examiner.issues)


def test_phase_reports_override_relabelled_aggregate_counts(tmp_path):
    directory, row, manifest = evidence(tmp_path, passes=False)
    receipt_path = directory / "private/trial/judgement/regression/receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["counts"]["passed"] = 1
    receipt["counts"]["failed"] = 0
    receipt_path.write_text(json.dumps(receipt))
    examiner = audit.Audit(directory)
    assert examiner.regression_judgement(row, manifest) is False
    assert any(i["code"] == "regression_receipt_counts" for i in examiner.issues)


def test_schema5_archive_freezes_common_judge_and_rejects_downgrade(
    tmp_path, monkeypatch
):
    from scripts import run_implementation_progress_live as live
    from minicode import config
    from tests.test_implementation_progress_live import fixture_source, offline_runtime

    for role in ("EXPLORE", "PLAN", "GENERAL"):
        monkeypatch.delenv(f"MINI_CODE_SUBAGENT_{role}_MAX_TURNS", raising=False)
    monkeypatch.setattr(config, "MINI_CODE_ENV_PATH", tmp_path / "missing.env")
    source, output = tmp_path / "history", tmp_path / "small"
    fixture_source(source)
    live.prepare(output, "engineering", source)
    offline_runtime(monkeypatch)
    live.freeze(output)
    manifest = live.shared.read_json(output / "manifest.json")
    examiner = audit.Audit(output)
    examiner.archived_hashes(manifest)
    assert examiner.issues == []
    assert manifest["judgementPolicy"] == judge.JUDGE_POLICY
    assert "implementation_judge.py" in manifest["harnessFiles"]
    manifest["schemaVersion"] = 4
    manifest["protocolVersion"] = "direct-workflow-v1"
    examiner = audit.Audit(output)
    examiner.archived_hashes(manifest)
    assert any(i["code"] == "frozen_protocol" for i in examiner.issues)
