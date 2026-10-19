"""Synthetic successor evidence: no model calls, oracle execution or live workspaces."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import audit_implementation_progress as audit

_SPEC = importlib.util.spec_from_file_location(
    "three_arm_audit_test_fixtures", Path(__file__).with_name("test_three_arm_audit.py")
)
old = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(old)
write, digest = old.write, audit.legacy.digest
WORKFLOW, EXECUTOR, ANALYST, OTHER = ["sub_" + c * 32 for c in "1234"]
SMALL = list(audit.ENGINEERING_CASES)
ALL_CASES = SMALL + [f"historical-{i}" for i in range(5)]


def save(path, manifest, results):
    write(path / "manifest.json", manifest)
    results["manifestSha256"] = digest((path / "manifest.json").read_bytes())
    write(path / "results.json", results)
    for row in results["trials"]:
        write(path / "trials" / (row["id"] + ".json"), row)


def experiment(parent, phase="engineering", name=None):
    """Build full phase schedule using real SQLite and separate frozen sources."""
    path = parent / (name or phase)
    path.mkdir()
    seed = parent / (path.name + "-seed")
    seed.mkdir()
    base_manifest, base_results = old.experiment(seed, owner_ack=True, rejections=True)
    source = parent / "historical-source"
    source.mkdir(exist_ok=True)
    source_tasks = {
        "cases": [
            {"id": c, "authorized_paths": ["minicode/example.py", "tests/check.py"]}
            for c in ALL_CASES
        ],
        "metadata": "retain full historical task content",
    }
    write(source / "tasks.json", source_tasks)
    write(
        source / "manifest.json",
        {"taskSha256": digest((source / "tasks.json").read_bytes())},
    )
    selected = SMALL if phase == "engineering" else ALL_CASES
    tasks = {
        **source_tasks,
        "cases": [c for c in source_tasks["cases"] if c["id"] in selected],
    }
    write(path / "tasks.json", tasks)
    harness = (
        "run_three_arm_real.py",
        "helper.py",
        "run_implementation_progress_live.py",
        "audit_implementation_progress.py",
        "audit_three_arm_real.py",
        "three_arm_protocol.py",
        "evaluation_transport.py",
    )
    files = {"scripts/" + n: b"# frozen placeholder; never execute\n" for n in harness}
    files.update(
        {
            "minicode/example.py": b"# historical production; never import\n",
            "scripts/run_three_arm_real.py": b"HARNESS_FILES = ('run_three_arm_real.py', 'helper.py')\n",
            "scripts/run_implementation_progress_live.py": (
                f"SUCCESSOR_HARNESS_FILES = {harness!r}\nSMALL_CASES = {tuple(SMALL)!r}\n"
            ).encode(),
            "scripts/three_arm_protocol.py": b"# task_result_owner_ack\n",
            "scripts/evaluation_transport.py": b"# admission_rejections\n",
            "analysis-plan.md": b"synthetic successor preregistration",
        }
    )
    with zipfile.ZipFile(path / "frozen-code.zip", "w") as bundle:
        for filename, content in files.items():
            bundle.writestr(filename, content)
    (path / "analysis-plan.md").write_bytes(files["analysis-plan.md"])
    arms, repeats = (
        (["single", "explicit"], 1)
        if phase == "engineering"
        else (list(audit.legacy.ARMS), 2)
    )
    schedule = [
        {"id": f"{case}-r{repeat}-{arm}", "case": case, "repeat": repeat, "arm": arm}
        for case in selected
        for repeat in range(1, repeats + 1)
        for arm in arms
    ]
    manifest = {
        **base_manifest,
        "schemaVersion": 3,
        "protocolVersion": "direct-workflow-v1",
        "phase": phase,
        "arms": arms,
        "repeats": repeats,
        "schedule": schedule,
        "explicitRecipe": "Coordinator-only parent (task/todo_write): direct workflow",
        "harnessFiles": list(harness),
        "runner": "scripts/run_implementation_progress_live.py",
        "harnessSha256": digest(
            b"".join(n.encode() + b"\0" + files["scripts/" + n] for n in harness)
        ),
        "frozenCodeArchiveSha256": digest((path / "frozen-code.zip").read_bytes()),
        "taskSha256": digest((path / "tasks.json").read_bytes()),
        "analysisPlanSha256": digest(files["analysis-plan.md"]),
        "sourceSuite": source.name,
        "sourceTaskSha256": digest((source / "tasks.json").read_bytes()),
        "sourceManifestSha256": digest((source / "manifest.json").read_bytes()),
        "reusedCalibrationCases": SMALL,
        "engineeringGate": None,
    }
    write(
        path / "source-provenance.json",
        {
            "sourceDirectory": str(source),
            "sourceSuite": source.name,
            "sourceTaskSha256": manifest["sourceTaskSha256"],
            "sourceManifestSha256": manifest["sourceManifestSha256"],
            "preparedTaskSha256": manifest["taskSha256"],
            "phase": phase,
            "selectedCases": selected,
        },
    )
    rows = []
    for assignment in schedule:
        base = next(r for r in base_results["trials"] if r["arm"] == assignment["arm"])
        row = {
            **deepcopy(base),
            **assignment,
            "changedSourceFiles": [],
            "actualAnalystLeafRuns": 0,
            "deliveredAnalystLeafResults": 0,
            "externalAnalysisLeafRuns": 0,
            "externalLeafRuns": 0,
        }
        root = path / "private" / row["id"]
        shutil.copytree(seed / "private" / base["id"], root)
        if row["arm"] == "explicit":
            write(root / "runtime-contract.json", {"tools": ["task", "todo_write"]})
        rows.append(row)
    results = {"expectedTrials": len(rows), "trials": rows}
    save(path, manifest, results)
    return path, manifest, results


def add_workflow(path, manifest, results, row, *, analyst=False):
    root = path / "private" / row["id"]
    text = "WORKFLOW FINALIZATION: verification failed after Executor returned"
    workflow = {
        "subagentId": WORKFLOW,
        "agentType": "workflow",
        "role": "workflow",
        "isContainer": True,
        "parentSubagentId": None,
        "startedEpoch": 1002,
        "returnedEpoch": 1008,
        "returned": True,
        "toolOk": False,
        "projected": None,
        "deliveredEpoch": 1008.1,
        "deliveryEvidence": "task_result_owner_ack",
        "resultSha256": digest(text.encode()),
    }
    executor = {
        "subagentId": EXECUTOR,
        "agentType": "general",
        "role": "Executor",
        "isContainer": False,
        "parentSubagentId": WORKFLOW,
        "startedEpoch": 1005,
        "returnedEpoch": 1007,
        "returned": True,
        "toolOk": False,
        "projected": None,
    }
    runs = [workflow, executor]
    if analyst:
        runs.append(
            {
                **executor,
                "subagentId": ANALYST,
                "role": "Analyst",
                "startedEpoch": 1003,
                "returnedEpoch": 1004,
            }
        )
    evidence = {"runs": runs, "deliveredIds": [WORKFLOW]}
    write(root / "collaboration.json", evidence)
    write(
        root / "transcript.json",
        [{"role": "tool_result", "toolName": "task", "content": text}],
    )
    with sqlite3.connect(root / "transport.sqlite") as db:
        first = json.loads(
            db.execute("SELECT data FROM requests WHERE call=1").fetchone()[0]
        )
        records = [first]
        for run in runs[1:]:
            record = {
                **deepcopy(first),
                "subagentId": run["subagentId"],
                "actorId": run["subagentId"],
                "role": run["role"],
                "agentType": run["agentType"],
                "startedEpoch": run["startedEpoch"],
            }
            db.execute(
                "INSERT INTO requests(status,charge_tokens,data) VALUES ('completed',120,?)",
                (json.dumps(record),),
            )
            records.append(record)
    count = len(records)
    row.update(audit.legacy.Audit(path).metrics(records, manifest["price"]))
    row.update(
        {
            "totalTokens": 120 * count,
            "budget": {
                "used_model_calls": count,
                "used_input_tokens": 100 * count,
                "used_output_tokens": 20 * count,
                "used_total_tokens": 120 * count,
            },
            "actualLeafRuns": count - 1,
            "leafModelCalls": count - 1,
            "deliveredLeafResults": count - 1,
            "successfulLeafRuns": 0,
            "readAnalysisLeafRuns": int(analyst),
            "implementationLeafRuns": 1,
            "collaborationExecuted": True,
            "collaborationProtocolSatisfied": True,
            "deliveredWorkflowResults": 1,
            "actualAnalystLeafRuns": int(analyst),
            "deliveredAnalystLeafResults": int(analyst),
            "externalAnalysisLeafRuns": 0,
            "externalLeafRuns": 0,
        }
    )
    save(path, manifest, results)
    return root, evidence


def codes(summary):
    return {i["code"] for i in summary["issues"] if i["severity"] == "error"}


def test_six_failed_trials_are_valid_observations_not_a_passed_engineering_gate(
    tmp_path,
):
    path, _, _ = experiment(tmp_path)
    summary = audit.Audit(path).run()
    assert summary["valid"], summary["issues"]
    assert summary["auditedTrials"] == summary["expectedTrials"] == 6
    assert not any(t["success"] for t in summary["trials"])
    assert all(
        t["recomputedCollaboration"]["actualLeafRuns"] == 0 for t in summary["trials"]
    )


@pytest.mark.parametrize("analyst", [False, True])
def test_executor_without_external_plan_and_failed_workflow_ack_counts(
    tmp_path, analyst
):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    add_workflow(path, manifest, results, row, analyst=analyst)
    summary = audit.Audit(path).run()
    assert summary["valid"], summary["issues"]
    value = summary["trials"][1]["recomputedCollaboration"]
    assert value["implementationLeafRuns"] == 1
    assert value["actualAnalystLeafRuns"] == int(analyst)
    assert value["deliveredAnalystLeafResults"] == int(analyst)
    assert value["collaborationProtocolSatisfied"] is True
    assert summary["trials"][1]["success"] is False


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("count", "collaboration_actualLeafRuns"),
        ("no_ack", "direct_delivery_without_owner_ack"),
        ("before_return", "direct_delivery_without_owner_ack"),
        ("missing_id", "collaboration_implementationLeafRuns"),
        ("wrong_parent", "collaboration_implementationLeafRuns"),
        ("out_of_window", "collaboration_implementationLeafRuns"),
        ("parent_tool", "coordinator_tool_request"),
        ("parent_contract", "coordinator_tool_contract"),
        ("started_only", "collaboration_actualLeafRuns"),
        ("failed_only", "collaboration_actualLeafRuns"),
    ],
)
def test_independent_delivery_and_runtime_restriction_tamper_detection(
    tmp_path, mutation, error
):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, evidence = add_workflow(path, manifest, results, row)
    workflow, executor = evidence["runs"]
    if mutation == "count":
        row["actualLeafRuns"] = 2
    elif mutation == "no_ack":
        workflow.pop("deliveryEvidence")
    elif mutation == "before_return":
        workflow["deliveredEpoch"] = 1007.5
    elif mutation == "missing_id":
        evidence["deliveredIds"] = []
    elif mutation == "wrong_parent":
        executor["parentSubagentId"] = OTHER
    elif mutation == "out_of_window":
        executor["returnedEpoch"] = 1009
    elif mutation == "parent_contract":
        write(
            root / "runtime-contract.json",
            {"tools": ["task", "todo_write", "edit_file"]},
        )
    else:
        with sqlite3.connect(root / "transport.sqlite") as db:
            call = 1 if mutation == "parent_tool" else 2
            record = json.loads(
                db.execute(
                    "SELECT data FROM requests WHERE call=?", (call,)
                ).fetchone()[0]
            )
            if mutation == "parent_tool":
                record["toolCalls"] = [{"name": "write_file"}]
            else:
                record["event"] = "started" if mutation == "started_only" else "failed"
                row[
                    "inFlightCalls" if mutation == "started_only" else "failedCalls"
                ] = 1
            db.execute(
                "UPDATE requests SET data=?,status=? WHERE call=?",
                (json.dumps(record), record["event"], call),
            )
    write(root / "collaboration.json", evidence)
    save(path, manifest, results)
    assert error in codes(audit.Audit(path).run())


def test_external_general_or_analysis_cannot_satisfy_direct_recipe(tmp_path):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, evidence = add_workflow(path, manifest, results, row, analyst=True)
    evidence["runs"][2]["parentSubagentId"] = None
    evidence["runs"][2]["role"] = "general"
    write(root / "collaboration.json", evidence)
    summary = audit.Audit(path).run()
    assert "collaboration_externalLeafRuns" in codes(summary)
    assert "collaboration_collaborationProtocolSatisfied" in codes(summary)
    assert summary["trials"][1]["recomputedCollaboration"]["externalLeafRuns"] == 1


def test_canonical_text_alone_is_not_host_delivery_and_compaction_preserves_ack(
    tmp_path,
):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, evidence = add_workflow(path, manifest, results, row)
    write(root / "transcript.json", [])
    summary = audit.Audit(path).run()
    assert summary["valid"], summary["issues"]
    assert any(i["code"] == "delivery_hash_unverified" for i in summary["issues"])
    evidence["deliveredIds"] = []
    write(root / "collaboration.json", evidence)
    write(
        root / "transcript.json",
        [
            {
                "role": "tool_result",
                "toolName": "task",
                "content": "=== SUBAGENT RESULT v1 ===\n"
                + json.dumps(old.projection(WORKFLOW, "workflow")),
            }
        ],
    )
    assert "collaboration_implementationLeafRuns" in codes(audit.Audit(path).run())


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("cost", "estimatedCostCny"),
        ("cleanup", "cleanup_incomplete"),
        ("source_changes", "changed_source_files"),
        ("frozen_hash", "harness_hash"),
        ("plan", "live_analysis_plan_hash"),
        ("provenance", "source_tasks_hash"),
        ("schedule", "unbalanced_schedule"),
    ],
)
def test_inherited_accounting_and_new_provenance_stay_strict(tmp_path, mutation, error):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][0]
    if mutation == "cost":
        row["estimatedCostCny"] = 0
    elif mutation == "cleanup":
        cleanup = {**row["processCleanup"], "cleanupComplete": False}
        row["processCleanup"] = cleanup
        write(path / "private" / row["id"] / "process-cleanup.json", cleanup)
    elif mutation == "source_changes":
        row["changedSourceFiles"] = ["tests/check.py"]
        row["changedFiles"] = ["tests/check.py"]
    elif mutation == "frozen_hash":
        manifest["harnessSha256"] = "f" * 64
    elif mutation == "plan":
        (path / "analysis-plan.md").write_text("modified after freeze")
    elif mutation == "provenance":
        source = tmp_path / "historical-source" / "tasks.json"
        source.write_text(source.read_text() + " ")
    else:
        manifest["schedule"][0]["repeat"] = 2
    save(path, manifest, results)
    summary = audit.Audit(path).run()
    assert error in codes(summary), summary["issues"]


def test_partial_mode_does_not_hide_missing_terminal_rows(tmp_path):
    path, manifest, results = experiment(tmp_path)
    missing = results["trials"].pop()
    (path / "trials" / (missing["id"] + ".json")).unlink()
    save(path, manifest, results)
    assert "incomplete_results" in codes(audit.Audit(path).run())
    partial = audit.Audit(path, allow_partial=True).run()
    assert partial["valid"], partial["issues"]
    assert {i["code"] for i in partial["issues"]} == {
        "incomplete_results",
        "unfinalized_ledger",
    }


def passing_engineering(parent):
    path, manifest, results = experiment(parent)
    for row in results["trials"]:
        if row["arm"] == "explicit":
            add_workflow(path, manifest, results, row)
        row.update(
            {
                "changedFiles": ["minicode/example.py"],
                "changedSourceFiles": ["minicode/example.py"],
                "oracleSuccess": True,
                "oraclePassed": True,
                "success": True,
            }
        )
        (path / "private" / row["id"] / "oracle.log").write_text(
            "synthetic external oracle passed\n"
        )
    save(path, manifest, results)
    summary = audit.Audit(path).run()
    assert summary["valid"], summary["issues"]
    # Cross-check the public gate interface, not the auditor's internal helper.
    from scripts.run_implementation_progress_live import gate

    gate_result = gate(path)
    assert gate_result["passed"], gate_result
    return path, gate_result


def test_full_48_requires_independently_validated_engineering_gate(tmp_path):
    engineering, gate_result = passing_engineering(tmp_path)
    path, manifest, results = experiment(tmp_path, "formal")
    manifest["engineeringGate"] = {"directory": str(engineering), **gate_result}
    save(path, manifest, results)
    summary = audit.Audit(path).run()
    assert summary["valid"], summary["issues"]
    assert summary["auditedTrials"] == summary["expectedTrials"] == 48
    # Engineering pass does not fabricate successes in the formal regression.
    assert not any(t["success"] for t in summary["trials"])
    manifest["engineeringGate"]["realRepairCount"] = 999
    save(path, manifest, results)
    assert "engineering_gate_realRepairCount" in codes(audit.Audit(path).run())


def test_failed_engineering_cannot_be_promoted_by_forging_pass_flag(tmp_path):
    engineering, _, _ = experiment(tmp_path)
    from scripts.run_implementation_progress_live import gate

    gate_result = gate(engineering)
    assert not gate_result["passed"]
    path, manifest, results = experiment(tmp_path, "formal")
    manifest["engineeringGate"] = {
        "directory": str(engineering),
        **gate_result,
        "passed": True,
    }
    save(path, manifest, results)
    summary = audit.Audit(path).run()
    assert "engineering_gate_failed" in codes(summary)
    assert "engineering_gate_passed" in codes(summary)


def test_formal_gate_does_not_follow_recursive_formal_reference(tmp_path):
    path, manifest, results = experiment(tmp_path, "formal")
    manifest["engineeringGate"] = {"directory": str(path), "passed": True}
    save(path, manifest, results)
    assert "invalid_engineering_gate_phase" in codes(audit.Audit(path).run())


def test_container_model_request_cannot_substitute_for_executor():
    workflow = {
        "subagentId": WORKFLOW,
        "agentType": "workflow",
        "role": "workflow",
        "isContainer": True,
        "startedEpoch": 1,
        "returnedEpoch": 3,
        "returned": True,
        "deliveredEpoch": 4,
        "deliveryEvidence": "task_result_owner_ack",
        "resultSha256": "a" * 64,
    }
    computed = audit.direct_collaboration(
        [workflow], [{"event": "completed", "subagentId": WORKFLOW}], [WORKFLOW]
    )
    assert computed["deliveredWorkflowResults"] == 1
    assert computed["actualLeafRuns"] == computed["leafModelCalls"] == 0
    assert computed["collaborationProtocolSatisfied"] is False


def test_executor_cannot_borrow_delivery_from_another_workflow(tmp_path):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, evidence = add_workflow(path, manifest, results, row)
    other = {**evidence["runs"][0], "subagentId": OTHER}
    evidence["runs"].append(other)
    evidence["deliveredIds"] = [OTHER]
    write(root / "collaboration.json", evidence)
    summary = audit.Audit(path).run()
    value = summary["trials"][1]["recomputedCollaboration"]
    assert value["deliveredWorkflowResults"] == 1
    assert value["implementationLeafRuns"] == 0
    assert "collaboration_collaborationProtocolSatisfied" in codes(summary)


def test_request_role_and_parent_permission_cannot_be_relabeled(tmp_path):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, _ = add_workflow(path, manifest, results, row)
    manifest["explicitRecipe"] = "pretend unrestricted"
    with sqlite3.connect(root / "transport.sqlite") as db:
        for call in (1, 2):
            record = json.loads(
                db.execute(
                    "SELECT data FROM requests WHERE call=?", (call,)
                ).fetchone()[0]
            )
            if call == 1:
                record["toolCalls"] = [{"name": "edit_file"}]
            else:
                record["role"] = "Analyst"
            db.execute(
                "UPDATE requests SET data=? WHERE call=?", (json.dumps(record), call)
            )
    save(path, manifest, results)
    assert {
        "explicit_recipe",
        "coordinator_tool_request",
        "leaf_request_role",
    } <= codes(audit.Audit(path).run())


def test_normal_raw_evidence_matches_runner_metrics_without_importing_it_in_auditor(
    tmp_path,
):
    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, evidence = add_workflow(path, manifest, results, row, analyst=True)
    with sqlite3.connect(root / "transport.sqlite") as db:
        records = [
            json.loads(r[0])
            for r in db.execute("SELECT data FROM requests ORDER BY call")
        ]
    from scripts.run_implementation_progress_live import collaboration_metrics

    expected = collaboration_metrics(
        evidence["runs"], records, [], evidence["deliveredIds"]
    )
    independently = audit.direct_collaboration(
        evidence["runs"], records, evidence["deliveredIds"]
    )
    assert independently == expected


def test_audit_hash_is_stable_across_python_hash_seeds(tmp_path):
    import os
    import subprocess
    import sys

    path, manifest, results = experiment(tmp_path)
    row = results["trials"][1]
    root, evidence = add_workflow(path, manifest, results, row)
    other = {**evidence["runs"][0], "subagentId": OTHER, "resultSha256": "f" * 64}
    evidence["runs"].append(other)
    evidence["deliveredIds"].append(OTHER)
    row["deliveredWorkflowResults"] = 2
    write(root / "collaboration.json", evidence)
    write(root / "transcript.json", [])
    save(path, manifest, results)
    command = [
        sys.executable,
        "-B",
        "-c",
        "import json,sys; from pathlib import Path; from scripts.audit_implementation_progress import Audit; print(json.dumps(Audit(Path(sys.argv[1])).run(), sort_keys=True))",
        str(path),
    ]
    outputs = [
        subprocess.check_output(
            command, env={**os.environ, "PYTHONHASHSEED": seed}, text=True
        )
        for seed in ("1", "999")
    ]
    assert json.loads(outputs[0])["valid"] is True
    assert outputs[0] == outputs[1]
