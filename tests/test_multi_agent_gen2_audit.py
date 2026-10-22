"""Synthetic audit evidence only; no provider, runner or archived code executes."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import time
import zipfile

import pytest

from scripts import audit_multi_agent_gen2 as audit


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def experiment(root):
    root.mkdir()
    shared = {"minicode/example.py": "def example(): return 'old'\n", "pyproject.toml": "# fixture\n"}
    cases, proofs = [], []
    for identity in audit.CASES:
        provenance = {"baselineCommit": audit.BASE, "referenceCommit": audit.REFERENCE,
                      "sourceBlobs": {"minicode/example.py": {
                          "baselineSha256": audit.legacy.digest(shared["minicode/example.py"].encode()),
                          "referenceSha256": audit.legacy.digest(b"def example(): return 'fixed'\n")}}}
        case = {"id": identity, "sourcePaths": ["minicode/example.py"], "provenance": provenance,
                "authorized_paths": ["minicode/example.py", "tests/test_eval_regression.py"],
                "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
                "files": {"tests/test_public.py": "def test_existing(): assert True\n"},
                "oracle_source": "# frozen synthetic oracle, never executed\n"}
        cases.append(case)
        proofs.append({"case": identity, "baselineCommit": audit.BASE, "referenceCommit": audit.REFERENCE,
                       "oracleSha256": audit.legacy.digest(case["oracle_source"].encode()),
                       "publicTestSha256": audit.legacy.digest(case["files"]["tests/test_public.py"].encode()),
                       "baselinePublic": {"returncode": 0}, "referencePublic": {"returncode": 0},
                       "baselineOracle": {"returncode": 1, "stdout": '{"ok":false,"errors":["target invariant"]}'},
                       "referenceOracle": {"returncode": 0, "stdout": '{"ok":true,"errors":[]}'}})
    tasks = {"generation": "gen-2", "shared_files": shared, "cases": cases}
    write(root / "tasks.json", tasks)
    write(root / "materials/offline-proof.json", proofs)
    write(root / "materials/source-manifest.json", {
        "baselineCommit": audit.BASE, "referenceCommit": audit.REFERENCE,
        "sharedFiles": {name: audit.legacy.digest(content.encode()) for name, content in shared.items()},
        "cases": [{"id": c["id"], "provenance": c["provenance"]} for c in cases],
        "offlineProofSha256": audit.legacy.digest((root / "materials/offline-proof.json").read_bytes()),
        "taskSha256": audit.legacy.digest((root / "tasks.json").read_bytes()), "modelCalls": 0})
    harness = ("__init__.py", "run_multi_agent_gen2.py", "audit_multi_agent_gen2.py",
               "run_implementation_progress_live.py", "audit_implementation_progress.py",
               "run_three_arm_real.py", "audit_three_arm_real.py", "implementation_judge.py",
               "three_arm_protocol.py", "evaluation_transport.py", "public_verification_fixtures.py")
    limits = {"maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5", "wallSeconds": 600,
              "maxOutputTokens": 8192, "requestTimeoutSeconds": 300, "idleTimeoutSeconds": 120, "connectTimeoutSeconds": 30}
    roles = {role: 0 for role in ("plan", "explore", "general")}
    judge = {"version": "fixture-common-judge-v1", "totalSeconds": 120,
             "regressionPath": "tests/test_eval_regression.py"}
    policy = {"registered": []}
    files = {"scripts/" + name: b"# archived fixture; never execute\n" for name in harness}
    files.update({"minicode/example.py": b"# execution host differs from task baseline\n",
                  "scripts/run_multi_agent_gen2.py": (f"PROTOCOL_VERSION={audit.PROTOCOL!r}\nCASES={audit.CASES!r}\n"
                      f"HARNESS_FILES={harness!r}\nLIMITS={limits!r}\nMODEL='glm-5.3-flash'\nSEED=20261023\nROLE_TURN_LIMITS={roles!r}\n").encode(),
                  "scripts/implementation_judge.py": f"JUDGE_POLICY={judge!r}\n".encode(),
                  "scripts/three_arm_protocol.py": b"# task_result_owner_ack\n",
                  "scripts/evaluation_transport.py": b"# admission_rejections\n",
                  "scripts/public_verification_fixtures.py": f"PUBLIC_FIXTURE_POLICY={policy!r}\n".encode()})
    with zipfile.ZipFile(root / "execution-host.zip", "w") as bundle:
        for name, data in sorted(files.items()):
            bundle.writestr(name, data)
    inventory = [{"path": name, "size": len(data), "sha256": audit.legacy.digest(data)} for name, data in sorted(files.items())]
    write(root / "execution-host-inventory.json", {"schemaVersion": 1, "files": inventory, "treeSha256": audit.canonical_digest(inventory)})
    (root / "analysis-plan.md").write_text("Synthetic six-cell preregistration\n")
    write(root / "environment.json", {"pythonVersion": "synthetic", "packages": []})
    classified = [{"nodeId": "test_public.py::test_existing", "outcome": "passed", "failureKind": None}]
    junit = "sha256:" + "a" * 64
    case_digest = "sha256:" + audit.canonical_digest({"junitDigest": junit, "cases": classified})
    pytest_report = {"collected": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0,
                     "reportDigest": junit, "caseReport": {"digest": case_digest, "cases": classified}}
    write(root / "public-preflight/receipt.json", {
        "policy": policy, "fixtures": [], "passed": True, "pytestReportRequired": True,
        "taskDocumentSha256": audit.canonical_digest(tasks),
        "cases": [{"case": c["id"], "argv": c["verification_argv"], "status": "passed",
                   "verification": {"status": "passed"}, "pytestReport": deepcopy(pytest_report),
                   "evidence": [{"argv": c["verification_argv"], "status": "passed", "exitCode": 0}]} for c in cases]})
    schedule = [{"case": case, "arm": arm, "repeat": 1, "id": f"{case}-r1-{arm}"}
                for case in audit.CASES for arm in ("single", "explicit")]
    random.Random(20261023).shuffle(schedule)
    production = sorted(name for name in files if name.startswith("minicode/") and name.endswith(".py"))
    manifest = {"schemaVersion": 1, "protocolVersion": audit.PROTOCOL, "phase": "gen2",
                "runner": "scripts/run_multi_agent_gen2.py", "arms": ["single", "explicit"], "repeats": 1,
                "scheduleSeed": 20261023, "schedule": schedule,
                "explicitRecipe": "Coordinator-only parent (task/todo_write): direct workflow",
                "harnessFiles": list(harness), "judgementPolicy": {**judge, "agentBudgetSeconds": 600},
                "roleTurnLimits": roles, "model": "glm-5.3-flash", "limits": limits, "temperature": 0, "concurrency": 1,
                "publicVerificationFixturePolicy": policy, "publicVerificationFixtures": [],
                "publicBaselineHashes": {c["id"]: {name: audit.legacy.digest(content.encode()) for name, content in sorted({**shared, **c["files"]}.items())} for c in cases},
                "oracleHashes": {c["id"]: audit.legacy.digest(c["oracle_source"].encode()) for c in cases},
                "productionSha256": audit.legacy.digest(b"".join(name.encode() + b"\0" + files[name] for name in production)),
                "harnessSha256": audit.legacy.digest(b"".join(name.encode() + b"\0" + files["scripts/" + name] for name in harness)),
                "executionHostTreeSha256": audit.canonical_digest(inventory)}
    for key, name in (("taskSha256", "tasks.json"), ("analysisPlanSha256", "analysis-plan.md"),
                      ("executionHostArchiveSha256", "execution-host.zip"), ("executionHostInventorySha256", "execution-host-inventory.json"),
                      ("environmentSha256", "environment.json"), ("publicPreflightSha256", "public-preflight/receipt.json")):
        manifest[key] = audit.legacy.digest((root / name).read_bytes())
    manifest["registrationFiles"] = {p.relative_to(root).as_posix(): audit.legacy.digest(p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}
    write(root / "manifest.json", manifest)
    return manifest, tasks, files


def codes(examiner):
    return {issue["code"] for issue in examiner.issues}


def test_preregistered_inputs_are_valid_partial_evidence_without_running_code(tmp_path):
    root = tmp_path / "run"
    experiment(root)
    result = audit.Audit(root, allow_partial=True).run()
    assert result["valid"], result["issues"]
    assert result["expectedTrials"] == 6
    assert result["auditedTrials"] == 0
    assert {issue["code"] for issue in result["issues"]} == {"incomplete_results"}
    assert audit.Audit(root).run()["valid"] is False


@pytest.mark.parametrize("mutation,expected", [
    ("source", "historical_shared_file_hashes"),
    ("wrong_parent", "historical_baseline_commit"),
    ("oracle_text", "proof_oracle_hash"),
    ("setup_failure", "offline_target_failure_unproven"),
    ("arbitrary_exit", "offline_baseline_must_fail"),
    ("empty_failure", "offline_target_failure_unproven"),
    ("public_failed", "offline_baselinePublic"),
])
def test_independent_provenance_rejects_relabelled_counterexamples(tmp_path, mutation, expected):
    root = tmp_path / "run"
    manifest, tasks, _files = experiment(root)
    source_path, proof_path = root / "materials/source-manifest.json", root / "materials/offline-proof.json"
    source, proof = json.loads(source_path.read_text()), json.loads(proof_path.read_text())
    if mutation == "source":
        tasks["shared_files"]["minicode/example.py"] = "fabricated broken baseline"
    elif mutation == "wrong_parent":
        source["baselineCommit"] = "f" * 40
    elif mutation == "oracle_text":
        tasks["cases"][0]["oracle_source"] += "# different oracle\n"
    elif mutation == "setup_failure":
        proof[0]["baselineOracle"]["stdout"] = "ModuleNotFoundError: missing dependency"
    elif mutation == "arbitrary_exit":
        proof[0]["baselineOracle"]["returncode"] = 2
    elif mutation == "empty_failure":
        proof[0]["baselineOracle"]["stdout"] = '{"ok":false,"errors":[]}'
    elif mutation == "public_failed":
        proof[0]["baselinePublic"]["returncode"] = 1
    write(proof_path, proof)
    source["offlineProofSha256"] = audit.legacy.digest(proof_path.read_bytes())
    write(source_path, source)
    examiner = audit.Audit(root)
    examiner.provenance(manifest, tasks)
    assert expected in codes(examiner)


def test_archive_inventory_and_analysis_plan_are_separately_bound(tmp_path):
    root = tmp_path / "run"
    manifest, _tasks, _files = experiment(root)
    (root / "analysis-plan.md").write_text("changed after freeze")
    with zipfile.ZipFile(root / "execution-host.zip", "a") as bundle:
        bundle.writestr("minicode/injected.py", "# never execute")
    examiner = audit.Audit(root)
    examiner.archived_hashes(manifest)
    assert {"archive_hash", "host_archive_coverage", "host_archive_inventory", "analysis_plan_hash"} <= codes(examiner)


def test_success_label_cannot_replace_public_test_case_evidence(tmp_path):
    root = tmp_path / "run"
    manifest, _tasks, _files = experiment(root)
    path = root / "public-preflight/receipt.json"
    receipt = json.loads(path.read_text())
    receipt["cases"][0]["pytestReport"]["caseReport"]["cases"] = []
    write(path, receipt)
    examiner = audit.Audit(root)
    examiner.archived_hashes(manifest)
    assert "public_pytest_case_evidence" in codes(examiner)


def test_schema_and_schedule_cannot_reuse_old_cases_or_reorder_cells(tmp_path):
    root = tmp_path / "run"
    manifest, tasks, _files = experiment(root)
    manifest["schedule"].reverse()
    manifest["schemaVersion"] = 5
    manifest["reusedCalibrationCases"] = ["real-vision-budget"]
    examiner = audit.Audit(root)
    examiner.design(manifest, {c["id"]: c for c in tasks["cases"]})
    examiner.engineering_gate(manifest)
    assert {"schema_version", "frozen_schedule_order", "gen2_no_reused_cases"} <= codes(examiner)


def test_execution_receipt_binds_real_git_registration_and_module_origins(tmp_path):
    root = tmp_path / "run"
    manifest, _tasks, files = experiment(root)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "run"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Audit Fixture", "-c", "user.email=audit@example.invalid",
                    "-c", "core.hooksPath=/dev/null", "commit", "--no-gpg-sign", "-qm", "synthetic preregistration"], check=True)
    commit = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    host = root / "private/execution-host"
    for name, content in files.items():
        path = host / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    trial = manifest["schedule"][0]["id"]
    receipt = {"schemaVersion": 1, "trialId": trial, "preregisteredCommit": commit,
               "manifestSha256": audit.legacy.digest((root / "manifest.json").read_bytes()),
               **{key: manifest[key] for key in ("executionHostInventorySha256", "executionHostTreeSha256", "executionHostArchiveSha256", "productionSha256", "harnessSha256")},
               "hostRoot": str(host), "entrypoint": str(host / "scripts/run_multi_agent_gen2.py"),
               "recordedAtEpoch": time.time(), "loadedModules": {"minicode.example": {
                   "path": str(host / "minicode/example.py"), "sha256": audit.legacy.digest(files["minicode/example.py"])}}}
    write(root / "private" / trial / "execution-host.json", receipt)
    examiner = audit.Audit(root)
    examiner.archived_hashes(manifest)
    examiner.execution_host({"id": trial}, manifest)
    assert examiner.issues == []
    receipt["loadedModules"]["minicode.example"]["path"] = str(tmp_path / "foreign/minicode/example.py")
    write(root / "private" / trial / "execution-host.json", receipt)
    examiner.execution_host({"id": trial}, manifest)
    assert "execution_host_foreign_module" in codes(examiner)
    (root / "analysis-plan.md").write_text("different from committed plan")
    second = audit.Audit(root)
    second.committed_registration(receipt, manifest, trial)
    assert "preregistered_git_blob_analysis-plan.md" in codes(second)


def test_generation_one_still_requires_real_generated_test_receipts(tmp_path, monkeypatch):
    from tests.test_implementation_regression_audit import evidence
    directory, row, manifest = evidence(tmp_path, passes=False)
    manifest["schemaVersion"] = 1

    def legacy_trial(self, row, *_args):
        self.trials.append({"success": row["success"], "diagnostics": {"terminalOutcomes": []}})

    monkeypatch.setattr(audit.legacy.Audit, "trial", legacy_trial)
    monkeypatch.setattr(audit.Audit, "execution_host", lambda *_args: None)
    examiner = audit.Audit(directory)
    examiner.trial(row, {"authorized_paths": []}, manifest)
    assert examiner.issues == []
    assert examiner.trials[-1]["success"] is False
    assert examiner.trials[-1]["diagnostics"]["terminalOutcomes"] == ["regression_tests_failed"]
