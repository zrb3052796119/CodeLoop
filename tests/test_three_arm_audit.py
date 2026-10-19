"""Synthetic independent evidence; no models, oracle runs or workspace edits."""

from __future__ import annotations

import importlib
import json
import sqlite3
import zipfile

import pytest

audit = importlib.import_module("scripts.audit_three_arm_real")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def experiment(path, partial=False, owner_ack=False, rejections=False):
    harness = ("run_three_arm_real.py", "helper.py")
    archive_files = {
        "minicode/example.py": b"# historical production; never import\n",
        "scripts/run_three_arm_real.py": f"HARNESS_FILES = {harness!r}\n".encode(),
        "scripts/helper.py": b"# historical helper\n",
        "scripts/evaluation_transport.py": b"# admission_rejections"
        if rejections
        else b"# legacy transport",
        "scripts/three_arm_protocol.py": b"# task_result_owner_ack"
        if owner_ack
        else b"# legacy protocol",
        "analysis-plan.md": b"synthetic preregistration",
    }
    with zipfile.ZipFile(path / "frozen-code.zip", "w") as bundle:
        for name, content in archive_files.items():
            bundle.writestr(name, content)
    task = {"id": "synthetic", "authorized_paths": ["module.py"]}
    write(path / "tasks.json", {"cases": [task]})
    schedule = [
        {"id": f"synthetic-r1-{a}", "case": "synthetic", "arm": a, "repeat": 1}
        for a in audit.ARMS
    ]
    manifest = {
        "schedule": schedule,
        "repeats": 1,
        "model": "synthetic-only",
        "resolvedTransportRouteSha256": "a" * 64,
        "limits": {"maxModelCalls": 40, "maxTokens": 500000, "maxOutputTokens": 8192},
        "price": {
            "perMillionInput": 0.8,
            "perMillionOutput": 2.8,
            "perMillionCacheRead": 0.23,
        },
        "frozenCodeArchiveSha256": audit.digest(
            (path / "frozen-code.zip").read_bytes()
        ),
        "productionSha256": audit.digest(
            b"minicode/example.py\0" + archive_files["minicode/example.py"]
        ),
        "harnessSha256": audit.digest(
            b"".join(
                n.encode() + b"\0" + archive_files["scripts/" + n] for n in harness
            )
        ),
        "analysisPlanSha256": audit.digest(archive_files["analysis-plan.md"]),
        "taskSha256": audit.digest((path / "tasks.json").read_bytes()),
    }
    write(path / "manifest.json", manifest)
    rows = []
    for assignment in schedule:
        if partial and assignment["arm"] != "explicit":
            continue
        root = path / "private" / assignment["id"]
        root.mkdir(parents=True)
        record = {
            "event": "completed",
            "model": manifest["model"],
            "routeSha256": manifest["resolvedTransportRouteSha256"],
            "startedEpoch": 1001,
            "usage": {
                "source": "provider",
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 40,
            },
            "providerUsage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 40},
            },
            "reservedTokens": 8192,
        }
        with sqlite3.connect(root / "transport.sqlite") as db:
            db.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, config TEXT)")
            db.execute(
                "CREATE TABLE requests (call INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, charge_tokens INTEGER, data TEXT)"
            )
            settings = {
                "expected_model": manifest["model"],
                "expected_route_sha256": manifest["resolvedTransportRouteSha256"],
                "max_model_calls": 40,
                "max_total_tokens": 500000,
                "max_output_tokens": 8192,
                "deadline_epoch": 1600,
            }
            db.execute("INSERT INTO settings VALUES (1, ?)", (json.dumps(settings),))
            db.execute(
                "INSERT INTO requests(status,charge_tokens,data) VALUES (?,?,?)",
                ("completed", 120, json.dumps(record)),
            )
            if rejections:
                db.execute(
                    "CREATE TABLE admission_rejections (rejection INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)"
                )
        cleanup = {
            "cleanupComplete": True,
            "survivorPids": [],
            "evidenceErrors": [],
            "processScanErrors": [],
        }
        write(root / "process-cleanup.json", cleanup)
        write(root / "collaboration.json", {"runs": [], "deliveredIds": []})
        write(root / "execution-start.json", {"epoch": 1000, "deadlineEpoch": 1600})
        row = {
            **assignment,
            **audit.Audit(path).metrics([record], manifest["price"]),
            "totalTokens": 120,
            "ledgerDamaged": False,
            "recoveryEvidenceErrors": [],
            "processCleanup": cleanup,
            "changedFiles": [],
            "unauthorizedChanges": [],
            "success": False,
            "oracleSuccess": False,
            "oraclePassed": False,
            "scopePassed": True,
            "collaborationProtocolSatisfied": False,
            **audit.independent_collaboration([], [], [], []),
            "budget": {
                "used_model_calls": 1,
                "used_input_tokens": 100,
                "used_output_tokens": 20,
                "used_total_tokens": 120,
            },
        }
        if rejections:
            row["admissionRejectedCount"] = 0
        write(path / "trials" / (row["id"] + ".json"), row)
        rows.append(row)
    results = {
        "manifestSha256": audit.digest((path / "manifest.json").read_bytes()),
        "expectedTrials": 3,
        "trials": rows,
    }
    write(path / "results.json", results)
    return manifest, results


def sync_row(path, results, row):
    write(path / "results.json", results)
    write(path / "trials" / (row["id"] + ".json"), row)


def change_request(
    path, row, *, event=None, inputs=None, cache_missing=False, unknown=False
):
    dbpath = path / "private" / row["id"] / "transport.sqlite"
    with sqlite3.connect(dbpath) as db:
        record = json.loads(db.execute("SELECT data FROM requests").fetchone()[0])
        if event:
            record["event"] = event
        if inputs is not None:
            record["usage"]["input_tokens"] = inputs
            record["providerUsage"]["prompt_tokens"] = inputs
        if cache_missing:
            record["usage"]["cache_read_tokens"] = None
            record["providerUsage"].pop("prompt_tokens_details")
        if unknown:
            record["usage"] = {
                "source": "unavailable",
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
            }
            record["providerUsage"] = None
        charge = (
            record["reservedTokens"]
            if unknown
            else record["usage"]["input_tokens"] + record["usage"]["output_tokens"]
        )
        db.execute(
            "UPDATE requests SET status=?,charge_tokens=?,data=?",
            (record["event"], charge, json.dumps(record)),
        )
    return record


def codes(summary, severity="error"):
    return {i["code"] for i in summary["issues"] if i["severity"] == severity}


def test_full_failure_experiment_valid_and_historical_hashes_do_not_use_live_source(
    tmp_path,
):
    experiment(tmp_path)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert summary["auditedTrials"] == 3
    assert all(not r["protocolSatisfied"] for r in summary["trials"])
    audit.write_report(tmp_path, summary)
    assert (tmp_path / "audit-report.md").exists()
    assert json.loads((tmp_path / "audit-summary.json").read_text())["valid"] is True


def test_selected_smoke_allowed_only_in_partial_mode(tmp_path):
    experiment(tmp_path, partial=True)
    assert audit.Audit(tmp_path, allow_partial=True).run()["valid"] is True
    assert "incomplete_results" in codes(audit.Audit(tmp_path).run())


@pytest.mark.parametrize("event", ["failed", "started"])
def test_failed_and_inflight_provider_receipts_count_despite_unsettled_budget(
    tmp_path, event
):
    manifest, results = experiment(tmp_path)
    row = results["trials"][0]
    record = change_request(tmp_path, row, event=event, inputs=140)
    row.update(audit.Audit(tmp_path).metrics([record], manifest["price"]))
    row["totalTokens"] = 160
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert "budget_receipt_difference" in codes(summary, "warning")
    assert summary["trials"][0]["inputTokens"] == 140
    assert summary["trials"][0]["usageComplete"] is True


def test_cache_only_missing_does_not_make_token_usage_incomplete(tmp_path):
    manifest, results = experiment(tmp_path)
    row = results["trials"][0]
    record = change_request(tmp_path, row, cache_missing=True)
    row.update(audit.Audit(tmp_path).metrics([record], manifest["price"]))
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    measured = summary["trials"][0]
    assert measured["tokenUsageComplete"] is True
    assert measured["cacheUsageComplete"] is False
    assert measured["estimatedCostCny"] is None
    assert measured["knownCostLowerBoundCny"] == pytest.approx(
        (100 * 0.23 + 20 * 2.8) / 1e6
    )


def test_unknown_receipt_not_zero_filled_and_keeps_reserved_tokens(tmp_path):
    manifest, results = experiment(tmp_path)
    row = results["trials"][0]
    record = change_request(tmp_path, row, event="started", unknown=True)
    row.update(audit.Audit(tmp_path).metrics([record], manifest["price"]))
    row["totalTokens"] = 0
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert summary["trials"][0]["unknownUsageCalls"] == 1
    assert summary["trials"][0]["knownCostLowerBoundCny"] is None


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ("row_tokens", "inputTokens"),
        ("budget", "budget_receipt_difference"),
        ("price", "estimatedCostCny"),
        ("recovery", "recovery_evidence_missing"),
        ("cleanup", "cleanup_incomplete"),
        ("scope", "scope_must_fail"),
        ("success", "success_formula"),
        ("provider", "provider_input_receipt"),
        ("deleted_request", "request_sequence_deleted_tail"),
    ],
)
def test_realistic_evidence_corruption_is_rejected(tmp_path, tamper, expected):
    _, results = experiment(tmp_path)
    row = results["trials"][0]
    root = tmp_path / "private" / row["id"]
    if tamper == "row_tokens":
        row["inputTokens"] = 99
    elif tamper == "budget":
        row["budget"]["used_input_tokens"] = 99
    elif tamper == "price":
        row["estimatedCostCny"] = 100
    elif tamper == "recovery":
        row["recoveryEvidenceErrors"] = ["collaboration.json"]
    elif tamper == "cleanup":
        row["processCleanup"]["cleanupComplete"] = False
        write(root / "process-cleanup.json", row["processCleanup"])
    elif tamper == "scope":
        row.update(
            changedFiles=["hidden-oracle.py"], unauthorizedChanges=["hidden-oracle.py"]
        )
    elif tamper == "success":
        row["success"] = True
    elif tamper == "provider":
        with sqlite3.connect(root / "transport.sqlite") as db:
            record = json.loads(db.execute("SELECT data FROM requests").fetchone()[0])
            record["providerUsage"]["prompt_tokens"] = 101
            db.execute("UPDATE requests SET data=?", (json.dumps(record),))
    else:
        with sqlite3.connect(root / "transport.sqlite") as db:
            db.execute("DELETE FROM requests")
    sync_row(tmp_path, results, row)
    assert expected in codes(audit.Audit(tmp_path).run())


def test_read_only_sqlite_does_not_update_evidence(tmp_path):
    experiment(tmp_path)
    paths = list(tmp_path.glob("private/*/transport.sqlite"))
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in paths}
    assert audit.Audit(tmp_path).run()["valid"] is True
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in paths} == before


def test_archive_and_manifest_tampering_is_rejected(tmp_path):
    manifest, results = experiment(tmp_path)
    manifest["harnessSha256"] = "wrong"
    manifest["productionSha256"] = "wrong"
    manifest["analysisPlanSha256"] = "wrong"
    write(tmp_path / "manifest.json", manifest)
    results["manifestSha256"] = audit.digest((tmp_path / "manifest.json").read_bytes())
    write(tmp_path / "results.json", results)
    errors = codes(audit.Audit(tmp_path).run())
    assert {"harness_hash", "production_hash", "analysis_plan_hash"} <= errors


def test_unfinalized_evidence_is_not_silently_omitted(tmp_path):
    _, results = experiment(tmp_path)
    omitted = results["trials"].pop()
    (tmp_path / "trials" / (omitted["id"] + ".json")).unlink()
    write(tmp_path / "results.json", results)
    summary = audit.Audit(tmp_path).run()
    assert {"incomplete_results", "unfinalized_ledger"} <= codes(summary)


def test_rerun_export_requests_not_present_in_sqlite_are_rejected(tmp_path):
    _, results = experiment(tmp_path)
    root = tmp_path / "private" / results["trials"][0]["id"]
    (root / "calls.jsonl").write_text(
        "\n".join(json.dumps({"call": n, "event": "started"}) for n in (1, 2))
    )
    assert "export_start_coverage" in codes(audit.Audit(tmp_path).run())


def test_completed_result_budget_must_match_persisted_execution(tmp_path):
    _, results = experiment(tmp_path)
    row = results["trials"][0]
    root = tmp_path / "private" / row["id"]
    write(root / "execution.json", {"budget": {"used_model_calls": 999}})
    assert "execution_budget_copy" in codes(audit.Audit(tmp_path).run())


def test_scope_violation_kept_as_valid_failure_not_removed(tmp_path):
    _, results = experiment(tmp_path)
    row = results["trials"][0]
    row.update(
        changedFiles=["outside.py"],
        unauthorizedChanges=["outside.py"],
        scopePassed=False,
    )
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert "scope_violation" in codes(summary, "warning")


PLAN, WORKFLOW, EXECUTOR, OTHER_WORKFLOW = ["sub_" + c * 32 for c in "1234"]


def projection(actor, kind="plan", outcome="completed"):
    return {
        "resultVersion": 1,
        "subagentId": actor,
        "agentType": kind,
        "outcome": outcome,
        "contractStatus": "fallback",
        "summary": "Host result",
        "files": [],
        "risks": [],
        "verification": {"status": "not_run", "checks": []},
    }


def collaborating_trial(path, strict=False, owner_ack=False):
    manifest, results = experiment(path, owner_ack=owner_ack)
    row = results["trials"][2]
    root = path / "private" / row["id"]
    runs = [
        {
            "subagentId": PLAN,
            "agentType": "plan",
            "role": "plan",
            "isContainer": False,
            "parentSubagentId": None,
            "startedEpoch": 1001,
            "returnedEpoch": 1002,
            "returned": True,
            "toolOk": True,
            "projected": projection(PLAN),
        },
        {
            "subagentId": WORKFLOW,
            "agentType": "workflow",
            "role": "workflow",
            "isContainer": True,
            "parentSubagentId": None,
            "startedEpoch": 1003,
            "returnedEpoch": 1006,
            "returned": True,
            "toolOk": True,
            "projected": projection(WORKFLOW, "workflow"),
        },
        {
            "subagentId": EXECUTOR,
            "agentType": "general",
            "role": "Executor",
            "isContainer": False,
            "parentSubagentId": WORKFLOW,
            "startedEpoch": 1004,
            "returnedEpoch": 1005,
            "returned": True,
            "toolOk": True,
            "projected": projection(EXECUTOR, "general"),
        },
    ]
    evidence = {"runs": runs, "deliveredIds": [PLAN, WORKFLOW]}
    write(root / "collaboration.json", evidence)
    with sqlite3.connect(root / "transport.sqlite") as db:
        original = json.loads(db.execute("SELECT data FROM requests").fetchone()[0])
        records = [original]
        for actor, epoch in ((PLAN, 1001.5), (EXECUTOR, 1004.5)):
            record = {
                **original,
                "subagentId": actor,
                "startedEpoch": epoch,
                "toolCalls": [{"name": "read_file"}],
            }
            records.append(record)
            db.execute(
                "INSERT INTO requests(status,charge_tokens,data) VALUES ('completed',120,?)",
                (json.dumps(record),),
            )
    row.update(audit.Audit(path).metrics(records, manifest["price"]))
    row.update(
        audit.independent_collaboration(runs, records, evidence["deliveredIds"], [])
    )
    row["totalTokens"] = 360
    row["budget"] = {
        "used_model_calls": 3,
        "used_input_tokens": 300,
        "used_output_tokens": 60,
        "used_total_tokens": 360,
    }
    if strict:
        manifest["explicitRecipe"] = (
            "Coordinator-only parent (task/todo_write): analysis then workflow."
        )
        write(path / "manifest.json", manifest)
        results["manifestSha256"] = audit.digest((path / "manifest.json").read_bytes())
        write(root / "runtime-contract.json", {"tools": ["task", "todo_write"]})
    sync_row(path, results, row)
    return manifest, results, row, root, evidence


def test_independent_protocol_delivered_and_strict_parent_does_not_ban_leaf_tools(
    tmp_path,
):
    collaborating_trial(tmp_path, strict=True)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert (
        summary["trials"][2]["recomputedCollaboration"][
            "collaborationProtocolSatisfied"
        ]
        is True
    )


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ("count", "collaboration_actualLeafRuns"),
        ("false_delivery", "collaboration_deliveredLeafResults"),
        ("order", "collaboration_collaborationProtocolSatisfied"),
        ("other_workflow", "collaboration_implementationLeafRuns"),
        ("started_only", "collaboration_actualLeafRuns"),
        ("unbound_id", "unbound_delivery_id"),
        ("parent_contract", "coordinator_tool_contract"),
        ("parent_call", "coordinator_tool_request"),
    ],
)
def test_mechanism_tampering_and_parent_tool_bypass_rejected(
    tmp_path, tamper, expected
):
    _, results, row, root, evidence = collaborating_trial(tmp_path, strict=True)
    if tamper == "count":
        row["actualLeafRuns"] = 3
    elif tamper == "false_delivery":
        evidence["deliveredIds"] = []
        write(
            root / "transcript.json",
            [
                {
                    "role": "tool",
                    "content": json.dumps({"subagentId": PLAN, "status": "running"}),
                }
            ],
        )
    elif tamper == "order":
        evidence["runs"][0]["returnedEpoch"] = 1004.5
    elif tamper == "other_workflow":
        other = {
            **evidence["runs"][1],
            "subagentId": OTHER_WORKFLOW,
            "projected": projection(OTHER_WORKFLOW, "workflow"),
        }
        evidence["runs"].append(other)
        evidence["deliveredIds"] = [PLAN, OTHER_WORKFLOW, EXECUTOR]
    elif tamper == "started_only":
        with sqlite3.connect(root / "transport.sqlite") as db:
            raw = json.loads(
                db.execute("SELECT data FROM requests WHERE call=3").fetchone()[0]
            )
            raw["event"] = "started"
            db.execute(
                "UPDATE requests SET status='started',data=? WHERE call=3",
                (json.dumps(raw),),
            )
        row["inFlightCalls"] = 1
    elif tamper == "unbound_id":
        evidence["deliveredIds"].append(OTHER_WORKFLOW)
    elif tamper == "parent_contract":
        write(
            root / "runtime-contract.json",
            {"tools": ["task", "todo_write", "read_file"]},
        )
    else:
        with sqlite3.connect(root / "transport.sqlite") as db:
            raw = json.loads(
                db.execute("SELECT data FROM requests WHERE call=1").fetchone()[0]
            )
            raw["toolCalls"] = [{"name": "read_file"}]
            db.execute("UPDATE requests SET data=? WHERE call=1", (json.dumps(raw),))
    write(root / "collaboration.json", evidence)
    sync_row(tmp_path, results, row)
    assert expected in codes(audit.Audit(tmp_path).run())


def test_verifier_failure_preserves_executed_protocol(tmp_path):
    _, results, row, root, evidence = collaborating_trial(tmp_path)
    evidence["runs"][1]["projected"]["outcome"] = "failed"
    evidence["runs"][1]["toolOk"] = False
    evidence["runs"][2]["projected"]["outcome"] = "failed"
    evidence["runs"][2]["toolOk"] = False
    write(root / "collaboration.json", evidence)
    row["successfulLeafRuns"] = 1
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert (
        summary["trials"][2]["recomputedCollaboration"][
            "collaborationProtocolSatisfied"
        ]
        is True
    )


def test_compacted_transcript_retains_host_acknowledged_delivery(tmp_path):
    _, _, _, root, _ = collaborating_trial(tmp_path)
    write(root / "transcript.json", [])
    assert audit.Audit(tmp_path).run()["valid"] is True


def test_canonical_tool_projection_counts_but_assistant_prose_does_not(tmp_path):
    _, results, row, root, evidence = collaborating_trial(tmp_path)
    evidence["deliveredIds"] = []
    write(root / "collaboration.json", evidence)
    messages = [
        {
            "role": "tool_result",
            "content": "=== SUBAGENT RESULT v1 ===\n" + json.dumps(r["projected"]),
        }
        for r in evidence["runs"][:2]
    ]
    write(root / "transcript.json", messages)
    assert audit.Audit(tmp_path).run()["valid"] is True
    for message in messages:
        message["role"] = "assistant"
    write(root / "transcript.json", messages)
    sync_row(tmp_path, results, row)
    assert "collaboration_collaborationProtocolSatisfied" in codes(
        audit.Audit(tmp_path).run()
    )


def test_null_error_class_budget_stop_is_diagnostic_without_changing_score(tmp_path):
    _, results = experiment(tmp_path)
    row = results["trials"][0]
    row["errorClass"] = None
    sync_row(tmp_path, results, row)
    root = tmp_path / "private" / row["id"]
    events = root / "journal" / "events.ndjson"
    events.parent.mkdir()
    events.write_text(
        json.dumps(
            {
                "type": "execution.completion_limited",
                "sequence": 7,
                "payload": {"reason": "turn_budget_exceeded: token budget exhausted"},
            }
        )
        + "\n"
    )
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    result = summary["trials"][0]
    assert result["success"] is False
    assert result["diagnostics"]["originalErrorClass"] is None
    assert result["diagnostics"]["stopEvidence"][0]["code"] == "budget_token"
    assert result["diagnostics"]["stopEvidence"][0]["authority"] == "host_journal"
    assert summary["trials"][1]["diagnostics"]["stopEvidence"][0]["code"] == "unknown"


def test_response_only_stop_is_not_promoted_to_host_verified_cause(tmp_path):
    _, results = experiment(tmp_path)
    root = tmp_path / "private" / results["trials"][0]["id"]
    (root / "response.txt").write_text(
        "Partial progress; stopped because turn_budget_exceeded: model call budget exhausted."
    )
    diag = audit.Audit(tmp_path).run()["trials"][0]["diagnostics"]
    assert diag["stopEvidence"][0]["code"] == "budget_calls"
    assert diag["stopEvidence"][0]["authority"] == "response_only_unconfirmed"


def owner_failure_trial(path):
    _, results, row, root, evidence = collaborating_trial(
        path, strict=True, owner_ack=True
    )
    messages = []
    for index, run in enumerate(evidence["runs"][:2]):
        raw = (
            ("=== SUBAGENT RESULT v1 ===\n" + json.dumps(run["projected"]))
            if index == 0
            else "WORKFLOW FINALIZATION\nSCHEDULER TRACE: execute_phase_failed"
        )
        run.update(
            resultSha256=audit.digest(raw.encode()),
            deliveredEpoch=run["returnedEpoch"] + 0.01,
            deliveryEvidence="task_result_owner_ack",
        )
        messages.append(
            {
                "role": "tool_result",
                "toolName": "task",
                "content": raw
                + ("\n\n[System note: retry if budget permits]" if index else ""),
            }
        )
    evidence["runs"][1].update(toolOk=False, projected=None)
    evidence["runs"][2].update(toolOk=False, projected=None)
    row["successfulLeafRuns"] = 1
    write(root / "collaboration.json", evidence)
    write(root / "transcript.json", messages)
    sync_row(path, results, row)
    return results, row, root, evidence


def test_owned_workflow_failure_receipt_is_delivery_without_success_or_projection(
    tmp_path,
):
    owner_failure_trial(tmp_path)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert summary["deliveryEvidenceMode"] == "task_result_owner_ack"
    result = summary["trials"][2]
    assert result["recomputedCollaboration"]["collaborationProtocolSatisfied"] is True
    assert result["recomputedCollaboration"]["deliveredLeafResults"] == 2
    assert result["success"] is False
    assert "delivery_hash_unverified" not in codes(summary, "warning")


def test_owned_delivery_survives_compaction_but_hash_unverifiable_is_explicit(tmp_path):
    _, _, root, _ = owner_failure_trial(tmp_path)
    write(root / "transcript.json", [])
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert "delivery_hash_unverified" in codes(summary, "warning")
    assert (
        summary["trials"][2]["recomputedCollaboration"][
            "collaborationProtocolSatisfied"
        ]
        is True
    )


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ("hash", "invalid_delivery_hash"),
        ("before_return", "delivery_before_return"),
        ("after_executor", "collaboration_collaborationProtocolSatisfied"),
        ("missing_ack", "delivery_without_host_result"),
    ],
)
def test_owner_delivery_identity_and_ack_order_are_enforced(tmp_path, tamper, expected):
    _, _, root, evidence = owner_failure_trial(tmp_path)
    if tamper == "hash":
        evidence["runs"][1]["resultSha256"] = "invalid"
    elif tamper == "before_return":
        evidence["runs"][1]["deliveredEpoch"] = 1000
    elif tamper == "after_executor":
        evidence["runs"][0]["deliveredEpoch"] = 1004.5
    else:
        evidence["runs"][1].pop("deliveryEvidence")
    write(root / "collaboration.json", evidence)
    assert expected in codes(audit.Audit(tmp_path).run())


def test_new_protocol_ignores_forged_projection_from_non_task_tool(tmp_path):
    _, _, _, root, evidence = collaborating_trial(tmp_path, owner_ack=True)
    evidence["deliveredIds"] = []
    write(root / "collaboration.json", evidence)
    write(
        root / "transcript.json",
        [
            {
                "role": "tool_result",
                "toolName": "read_file",
                "content": "=== SUBAGENT RESULT v1 ===\n" + json.dumps(r["projected"]),
            }
            for r in evidence["runs"][:2]
        ],
    )
    assert "collaboration_collaborationProtocolSatisfied" in codes(
        audit.Audit(tmp_path).run()
    )


def test_legacy_archive_cannot_reinterpret_new_callback_fields(tmp_path):
    _, _, root, evidence = owner_failure_trial(tmp_path)
    check = audit.Audit(tmp_path)
    check.owner_ack = False  # Same record under the older frozen protocol.
    row = json.loads((tmp_path / "trials" / (root.name + ".json")).read_text())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    records = check.ledger(root, manifest, row["id"])
    check.collaboration(root, row, records, manifest)
    assert any(i["code"] == "delivery_without_host_result" for i in check.issues)
    assert evidence["runs"][1]["projected"] is None


def test_guardian_and_transport_timeout_are_cooccurring_diagnostics(tmp_path):
    _, results = experiment(tmp_path)
    row = results["trials"][0]
    root = tmp_path / "private" / row["id"]
    record = change_request(tmp_path, row, event="failed")
    record["errorClass"] = "EvaluationIdleTimeout"
    with sqlite3.connect(root / "transport.sqlite") as db:
        db.execute("UPDATE requests SET data=?", (json.dumps(record),))
    row["failedCalls"] = 1
    row["processCleanup"]["deadlineReachedEpoch"] = 1600
    write(root / "process-cleanup.json", row["processCleanup"])
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    reasons = {e["code"] for e in summary["trials"][0]["diagnostics"]["stopEvidence"]}
    assert reasons == {"guardian_hard_stop", "request_timeout"}


@pytest.mark.parametrize(
    "started,deadline,elapsed,global_deadline,expected",
    [
        (1000, 1179.997, 180.005, 1600, "request_deadline_per_request"),
        (1599.282, 1600, 0.721, 1600, "request_deadline_global"),
        (1420, 1600, 180, 1600, "request_deadline"),  # Both limits coincide.
        (1420.5, 1600, 179.5, 1600, "request_deadline"),  # Ambiguous near tie.
        (1000, 1179.997, 180.005, 1180.5, "request_deadline"),
        (1000, 1120, 120, 1600, "request_deadline"),  # Earlier caller deadline.
        (1000, 1180, 20, 1600, "request_deadline"),  # Not elapsed.
        (1599.282, 1600, 0.01, 1600, "request_deadline"),
        (1000, 1180, 185, 1600, "request_deadline"),  # Timing disagreement.
        (1000, 1180, 180, 1179, "request_deadline"),  # Global already exceeded.
        (1600, 1600, 0.001, 1600, "request_deadline"),
    ],
)
def test_deadline_diagnostic_distinguishes_only_supported_timing_boundaries(
    started, deadline, elapsed, global_deadline, expected
):
    result = audit.request_deadline_diagnostic(
        {
            "startedEpoch": started,
            "requestDeadlineEpoch": deadline,
            "seconds": elapsed,
        },
        {"deadlineEpoch": global_deadline},
        {"limits": {"requestTimeoutSeconds": 180}},
    )
    assert result["code"] == expected
    assert result["timing"]["requestTimeoutSeconds"] == 180
    if started == 1420:
        assert result["boundaryEvidence"] == "coincident_deadline_boundaries"


@pytest.mark.parametrize(
    "missing", ["startedEpoch", "requestDeadlineEpoch", "seconds", "global", "limit"]
)
def test_deadline_diagnostic_missing_timing_stays_unspecified(missing):
    request = {"startedEpoch": 1000, "requestDeadlineEpoch": 1180, "seconds": 180}
    execution = {"deadlineEpoch": 1600}
    manifest = {"limits": {"requestTimeoutSeconds": 180}}
    if missing == "global":
        execution.clear()
    elif missing == "limit":
        manifest.clear()
    else:
        request.pop(missing)
    result = audit.request_deadline_diagnostic(request, execution, manifest)
    assert result["code"] == "request_deadline"


@pytest.mark.parametrize(
    "invalid", [True, "180", None, 0, -1, float("inf"), float("nan")]
)
def test_deadline_diagnostic_invalid_elapsed_is_not_evidence(invalid):
    result = audit.request_deadline_diagnostic(
        {"startedEpoch": 1000, "requestDeadlineEpoch": 1180, "seconds": invalid},
        {"deadlineEpoch": 1600},
        {"limits": {"requestTimeoutSeconds": 180}},
    )
    assert result["code"] == "request_deadline"


@pytest.mark.parametrize("global_deadline", [False, True])
def test_model_call_deadline_is_explained_without_mutating_score_or_receipts(
    tmp_path, global_deadline
):
    manifest, results = experiment(tmp_path)
    manifest["limits"]["requestTimeoutSeconds"] = 180
    write(tmp_path / "manifest.json", manifest)
    results["manifestSha256"] = audit.digest((tmp_path / "manifest.json").read_bytes())
    row = results["trials"][0]
    root = tmp_path / "private" / row["id"]
    record = change_request(tmp_path, row, event="failed")
    record.update(
        errorClass="ModelCallDeadlineExceeded",
        startedEpoch=1599.282 if global_deadline else 1001,
        requestDeadlineEpoch=1600 if global_deadline else 1180.997,
        seconds=0.721 if global_deadline else 180.005,
    )
    with sqlite3.connect(root / "transport.sqlite") as db:
        db.execute("UPDATE requests SET data=?", (json.dumps(record),))
    row["failedCalls"] = 1
    row["errorClass"] = (
        "HardTimeout" if global_deadline else "AgentTurnDeadlineExceeded"
    )
    if global_deadline:
        row["processCleanup"]["deadlineReachedEpoch"] = 1600.009
        write(root / "process-cleanup.json", row["processCleanup"])
    sync_row(tmp_path, results, row)
    original_row = (tmp_path / "trials" / (row["id"] + ".json")).read_bytes()
    original_db = (root / "transport.sqlite").read_bytes()
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert summary["issues"] == []
    result = summary["trials"][0]
    assert result["success"] is False
    assert result["inputTokens"] == 100
    assert result["usageComplete"] is True
    assert result["diagnostics"]["originalErrorClass"] == row["errorClass"]
    evidence = [
        e
        for e in result["diagnostics"]["stopEvidence"]
        if e["source"] == "transport.sqlite"
    ]
    assert len(evidence) == 1
    assert evidence[0]["evidence"] == "ModelCallDeadlineExceeded"
    assert evidence[0]["code"] == (
        "request_deadline_global" if global_deadline else "request_deadline_per_request"
    )
    assert (tmp_path / "trials" / (row["id"] + ".json")).read_bytes() == original_row
    assert (root / "transport.sqlite").read_bytes() == original_db


def test_guardian_presence_does_not_assign_global_cause_to_earlier_request(tmp_path):
    root = tmp_path / "private" / "synthetic"
    write(root / "execution-start.json", {"deadlineEpoch": 1600})
    row = {"id": "synthetic", "arm": "single", "errorClass": None}
    record = {
        "call": 1,
        "event": "failed",
        "errorClass": "ModelCallDeadlineExceeded",
        "startedEpoch": 1000,
        "requestDeadlineEpoch": 1180,
        "seconds": 180,
    }
    diag = audit.Audit(tmp_path).stop_diagnostics(
        root,
        row,
        [record],
        {"deadlineReachedEpoch": 1600.01},
        {"limits": {"requestTimeoutSeconds": 180}},
    )
    assert [e["code"] for e in diag["stopEvidence"]] == [
        "guardian_hard_stop",
        "request_deadline_per_request",
    ]
    (root / "execution-start.json").unlink()
    diag = audit.Audit(tmp_path).stop_diagnostics(
        root,
        row,
        [record],
        {"deadlineReachedEpoch": 1600.01},
        {"limits": {"requestTimeoutSeconds": 180}},
    )
    assert diag["stopEvidence"][1]["code"] == "request_deadline"


def rejected_trial(path, frozen_support=True):
    manifest, results = experiment(path, rejections=frozen_support)
    row = results["trials"][0]
    root = path / "private" / row["id"]
    refusal = {
        "event": "admission_rejected",
        "reason": "token_budget",
        "rejectedEpoch": 1002,
        "reservedTokens": 500001,
        "currentRequests": 1,
        "currentChargedTokens": 120,
        "maxModelCalls": 40,
        "maxTotalTokens": 500000,
        "deadlineEpoch": 1600,
        "estimatedInputTokens": 491809,
        "maxOutputTokens": 8192,
        "actorId": "main",
        "subagentId": None,
        "role": "main",
        "agentType": "main",
        "model": manifest["model"],
        "routeSha256": manifest["resolvedTransportRouteSha256"],
        "pid": 123,
        "thread": "MainThread",
        "parentToolOperationId": None,
    }
    with sqlite3.connect(root / "transport.sqlite") as db:
        if not frozen_support:
            db.execute(
                "CREATE TABLE admission_rejections (rejection INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)"
            )
        db.execute(
            "INSERT INTO admission_rejections(data) VALUES (?)", (json.dumps(refusal),)
        )
    row["budget"]["used_model_calls"] = 2
    row["admissionRejectedCount"] = 1
    sync_row(path, results, row)
    return results, row, root, refusal


def test_exact_durable_pre_http_refusal_explains_only_budget_call_counter(tmp_path):
    rejected_trial(tmp_path)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is True
    assert "budget_calls_include_admission_rejections" in codes(summary, "warning")
    trial = summary["trials"][0]
    assert trial["modelCalls"] == 1
    assert trial["totalTokens"] == 120
    assert trial["usageComplete"] is True
    assert trial["estimatedCostCny"] == pytest.approx(0.0001132)
    assert trial["admissionRejectedCount"] == trial["validatedAdmissionRejections"] == 1
    assert (
        trial["diagnostics"]["stopEvidence"][0]["code"]
        == "transport_admission_token_budget"
    )


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ("count", "budget_receipt_difference"),
        ("tokens", "budget_receipt_difference"),
        ("priority", "rejection_reason_threshold"),
        ("within_limit", "rejection_without_exceeded_limit"),
        ("route", "rejection_routeSha256"),
        ("actor", "unbound_rejection_actor"),
        ("usage", "rejection_contains_http_evidence"),
        ("row_count", "admission_rejected_count"),
        ("production_not_counted", "admission_budget_count_mismatch"),
    ],
)
def test_refusal_evidence_is_not_a_general_budget_mismatch_waiver(
    tmp_path, tamper, expected
):
    results, row, root, refusal = rejected_trial(tmp_path)
    if tamper == "count":
        row["budget"]["used_model_calls"] = 3
    elif tamper == "tokens":
        row["budget"]["used_input_tokens"] = 99
    elif tamper == "priority":
        refusal["reason"] = "request_budget"
    elif tamper == "within_limit":
        refusal.update(reservedTokens=8193, estimatedInputTokens=1)
    elif tamper == "route":
        refusal["routeSha256"] = "b" * 64
    elif tamper == "actor":
        refusal["actorId"] = "imaginary"
    elif tamper == "usage":
        refusal["providerUsage"] = {"prompt_tokens": 100}
    elif tamper == "row_count":
        row["admissionRejectedCount"] = 0
    else:
        row["budget"]["used_model_calls"] = 1
    with sqlite3.connect(root / "transport.sqlite") as db:
        db.execute("UPDATE admission_rejections SET data=?", (json.dumps(refusal),))
    sync_row(tmp_path, results, row)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"] is False
    assert expected in codes(summary)


def test_old_frozen_archive_cannot_be_repaired_by_inserting_refusal_row(tmp_path):
    rejected_trial(tmp_path, frozen_support=False)
    summary = audit.Audit(tmp_path).run()
    assert {"unregistered_admission_table", "budget_receipt_difference"} <= codes(
        summary
    )


def test_unproven_complete_budget_call_difference_stays_error(tmp_path):
    _, results = experiment(tmp_path)
    row = results["trials"][0]
    row["budget"]["used_model_calls"] = 2
    sync_row(tmp_path, results, row)
    assert "budget_receipt_difference" in codes(audit.Audit(tmp_path).run())
