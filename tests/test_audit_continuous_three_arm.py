"""Synthetic artifacts only: no task, configuration, provider or judge is loaded."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("independent_continuous_audit", Path(os.environ.get("AUDITOR_PATH", ROOT / "scripts" / "audit_continuous_three_arm.py")))
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
SECRET = "NEVER_OUTPUT_MODEL_REASONING_OR_ORACLE_TEXT"


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def get(root, name):
    return json.loads((root / name).read_text())


def change(root, name, transform):
    value = get(root, name)
    transform(value)
    put(root, name, value)


def _request(call, actor, role, model="glm-5.3-flash"):
    return {"call": call, "status": "completed", "actorId": actor,
            "subagentId": None if role == "main" else actor, "role": role, "model": model,
            "routeSha256": "b" * 64, "startedEpoch": 100 + call, "seconds": 1,
            "maxOutputTokens": 131072, "requestDeadlineEpoch": None,
            "toolNames": ["task", "todo_write"] if role == "main" else ["read_file"],
            "usage": {"source": "provider", "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 20},
            "toolCalls": [], "reasoning": SECRET, "response": SECRET,
            "messages": [{"content": SECRET}]}


def write_requests(root, identifier, requests):
    path = root / "private" / identifier / "transport.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE IF EXISTS requests")
        connection.execute("CREATE TABLE requests(call INTEGER PRIMARY KEY,status TEXT,data TEXT)")
        for row in requests:
            connection.execute("INSERT INTO requests VALUES(?,?,?)", (row["call"], row["status"], json.dumps(row)))


def meter(root, identifier, requests):
    """Fixture arithmetic independent of the auditor's reader/calculator."""
    known = [r for r in requests if r.get("usage", {}).get("source") == "provider"]
    inputs = sum(r["usage"]["input_tokens"] for r in known)
    outputs = sum(r["usage"]["output_tokens"] for r in known)
    all_known = len(known) == len(requests)
    caches_known = all_known and all(r["usage"].get("cache_read_tokens") is not None for r in known)
    cached = sum(r["usage"].get("cache_read_tokens") or 0 for r in known)
    lower = sum(((r["usage"]["input_tokens"] - (r["usage"]["input_tokens"] if r["usage"].get("cache_read_tokens") is None else r["usage"]["cache_read_tokens"])) * .8
                 + (r["usage"]["input_tokens"] if r["usage"].get("cache_read_tokens") is None else r["usage"]["cache_read_tokens"]) * .23
                 + r["usage"]["output_tokens"] * 2.8) / 1000000 for r in known)
    change(root, f"trials/{identifier}.json", lambda row: row.update(
        modelCalls=len(requests), failedCalls=sum(r["status"] == "failed" for r in requests),
        inFlightCalls=sum(r["status"] == "started" for r in requests),
        unknownUsageCalls=len(requests)-len(known), tokenUsageComplete=all_known,
        cacheUsageComplete=caches_known, usageComplete=all_known and caches_known,
        totalTokens=inputs+outputs if all_known else None,
        inputTokens=inputs if all_known else None, outputTokens=outputs if all_known else None,
        cacheReadTokens=cached if caches_known else None, knownTokenLowerBound=inputs+outputs,
        estimatedCostCny=lower if caches_known else None, knownCostLowerBoundCny=lower,
        delegationAttempts=sum(tool["name"] == "task" for r in requests for tool in r.get("toolCalls", []))))


@pytest.fixture
def sample(tmp_path):
    files = {"minicode/unit.py": b"raise RuntimeError('archive must never execute')\n",
             "scripts/run_continuous_three_arm.py": b"raise RuntimeError('no runner import')\n"}
    baseline = {"candidate.py": ["file", "f" * 64]}
    plan = b"Synthetic offline plan. No real task details.\n"
    task = b"opaque synthetic task with non-JSON sentinel: " + SECRET.encode()
    (tmp_path / "tasks.json").write_bytes(task)
    (tmp_path / "analysis-plan.md").write_bytes(plan)
    put(tmp_path, "public-preflight/receipt.json", {"passed": True, "machineReportRequired": True})
    with zipfile.ZipFile(tmp_path / "frozen-code.zip", "w") as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
        archive.writestr("analysis-plan.md", plan)
    policy = {"runtime": {"maxOutputTokens": 131072, "modelTimeoutSeconds": None,
                          "temperature": 0, "modelMaxRetries": 0, "subagentRoutingEnabled": False},
              "policySourceSha256": {"minicode/unit.py": audit.digest(files["minicode/unit.py"])}}
    schedule = [{"case": audit.CASE, "repeat": 1, "arm": arm, "id": f"{audit.CASE}-r1-{arm}"} for arm in audit.ORDER]
    manifest = {"protocolVersion": audit.PROTOCOL, "phase": "pilot", "seed": 20261017,
                "schedule": schedule, "arms": ["single", "auto", "explicit"], "repeats": 1, "concurrency": 1,
                "limits": deepcopy(audit.LIMITS), "parentMaxSteps": None, "boundedImplementationPhases": False,
                "roleTurnLimits": {"explore": 0, "plan": 0, "general": 0}, "model": "glm-5.3-flash", "temperature": 0,
                "sameBudgetComparison": False, "publicPytestReportRequired": True,
                "implementationCapabilities": "shared-public-checks-source-context-and-fixtures-v2",
                "routeSha256": "a"*64, "resolvedTransportRouteSha256": "b"*64,
                "sourceTaskSha256": "c"*64, "sourceManifestSha256": "d"*64,
                "commonRequirementSha256": "e"*64, "explicitRecipeSha256": "f"*64,
                "baselineFilesSha256": audit.digest(audit.canonical(baseline)),
                "taskSha256": audit.digest(task), "analysisPlanSha256": audit.digest(plan),
                "frozenCodeArchiveSha256": audit.file_digest(tmp_path / "frozen-code.zip"),
                "publicPreflightSha256": audit.file_digest(tmp_path / "public-preflight/receipt.json"),
                "productionSha256": audit.digest(b"minicode/unit.py\0"+files["minicode/unit.py"]),
                "harnessFiles": ["run_continuous_three_arm.py"],
                "harnessSha256": audit.digest(b"run_continuous_three_arm.py\0"+files["scripts/run_continuous_three_arm.py"]),
                "requestPolicy": policy, "requestPolicySha256": audit.digest(json.dumps(policy, sort_keys=True).encode()),
                "price": {"currency": "CNY", "perMillionInput": .8, "perMillionOutput": 2.8, "perMillionCacheRead": .23},
                "judgementPolicy": {"version": "common-generated-regression-v1", "totalSeconds": 120,
                    "regressionPath": "tests/test_eval_regression.py", "minimumPassed": 1,
                    "requireEveryCollectedPassed": True, "allowSkipOrXfail": False,
                    "separateUnfilteredRegressionRun": True, "deliveredCopiesOnly": True,
                    "receiptExitAndCleanupRequired": True, "agentBudgetSeconds": 1800}}
    put(tmp_path, "manifest.json", manifest)
    put(tmp_path, "source-provenance.json", {"preparedTaskSha256": manifest["taskSha256"],
        "sourceTaskSha256": manifest["sourceTaskSha256"], "sourceManifestSha256": manifest["sourceManifestSha256"],
        "baselineFilesSha256": manifest["baselineFilesSha256"]})
    all_requests = {}
    for index, trial in enumerate(schedule):
        identifier = trial["id"]
        prefix = f"private/{identifier}/"
        put(tmp_path, prefix+"baseline-files.json", baseline)
        put(tmp_path, prefix+"execution-start.json", {"epoch": 100+index*100, "deadlineEpoch": None})
        put(tmp_path, prefix+"runtime-contract.json", {"model": "glm-5.3-flash", "routeSha256": "a"*64, "systemPrompt": SECRET})
        put(tmp_path, prefix+"transport-config.json", {"expected_model": "glm-5.3-flash", "expected_route_sha256": "b"*64,
            "deadline_epoch": None, "max_model_calls": None, "max_total_tokens": None,
            "request_timeout_seconds": None, "max_output_tokens": 131072, "idle_timeout_seconds": 120, "connect_timeout_seconds": 30})
        put(tmp_path, prefix+"process-cleanup.json", {"cleanupComplete": True, "survivorPids": []})
        requests = [_request(1, "main", "main")]
        if trial["arm"] == "single":
            requests[0]["toolNames"] = ["read_file"]
        if trial["arm"] == "explicit":
            requests.extend([_request(2, "executor-a", "executor"), _request(3, "reviewer-a", "reviewer")])
            requests[0]["toolCalls"] = [{"name": "task", "arguments": {"private": SECRET}}]
            runs = [{"subagentId": "workflow-a", "isContainer": True, "agentType": "workflow", "returned": True,
                     "startedEpoch": 101, "returnedEpoch": 110, "deliveredEpoch": 111,
                     "deliveryEvidence": "task_result_owner_ack", "resultSha256": "1"*64},
                    {"subagentId": "executor-a", "isContainer": False, "role": "executor", "returned": True,
                     "parentSubagentId": "workflow-a", "startedEpoch": 102, "returnedEpoch": 105},
                    {"subagentId": "reviewer-a", "isContainer": False, "role": "reviewer", "returned": True,
                     "parentSubagentId": "workflow-a", "startedEpoch": 106, "returnedEpoch": 109}]
            collab = {"runs": runs, "deliveredIds": ["workflow-a"]}
        else:
            collab = {"runs": [], "deliveredIds": []}
        put(tmp_path, prefix+"collaboration.json", collab)
        put(tmp_path, f"trials/{identifier}.json", {**trial, "success": True, "regressionTestPassed": True,
            "scopePassed": True, "oraclePassed": True, "judgement": {"cleanupComplete": True, "details": SECRET},
            "processCleanup": {"cleanupComplete": True}, "durationSeconds": 20, "judgeSeconds": 3,
            "budget": {"limit_total_tokens": None, "limit_model_calls": None, "limit_cost_usd": None},
            "collaborationProtocolSatisfied": trial["arm"] == "explicit"})
        write_requests(tmp_path, identifier, requests)
        meter(tmp_path, identifier, requests)
        all_requests[trial["arm"]] = requests
    return tmp_path, all_requests


def codes(result):
    return {issue["code"] for issue in result["issues"]}


def test_offline_audit_separates_success_from_actual_delegation_and_leaks_no_private_text(sample, monkeypatch):
    root, _ = sample
    original = Path.read_text
    def guarded(self, *args, **kwargs):
        assert self.name not in {"tasks.json", "oracle.py", "transcript.json", "response.txt", "calls.jsonl"}
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]
    assert result["draftOnly"] is False
    assert len(result["trials"]) == 3 and result["allCellsTerminal"]
    explicit, single, auto = result["trials"]
    assert explicit["actualDelegationObserved"] and explicit["ownerAcknowledgedWorkflows"] == 1
    assert single["publicChecksPassed"] and auto["publicChecksPassed"]
    assert not single["actualDelegationObserved"] and not auto["actualDelegationObserved"]
    assert SECRET not in json.dumps(result)


def test_unselected_runtime_prompt_and_judgement_details_are_never_decoded(sample, monkeypatch):
    root, _ = sample
    original = audit.json.loads

    def public_only(value, *args, **kwargs):
        assert SECRET not in value, "non-whitelisted contents reached the audit JSON decoder"
        return original(value, *args, **kwargs)

    monkeypatch.setattr(audit.json, "loads", public_only)
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]


@pytest.mark.parametrize("status", ["failed", "started"])
def test_failed_or_unfinished_unknown_request_is_retained_and_never_charged_zero(sample, status):
    root, groups = sample
    identifier = f"{audit.CASE}-r1-auto"
    rows = groups["auto"]
    extra = _request(2, "main", "main")
    extra.update(status=status, usage={"source": "unknown"})
    rows.append(extra)
    write_requests(root, identifier, rows)
    meter(root, identifier, rows)
    change(root, f"trials/{identifier}.json", lambda row: row.update(success=False, regressionTestPassed=False, oraclePassed=False))
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]
    row = result["trials"][2]
    assert row["modelCalls"] == 2 and row["unknownUsageCalls"] == 1
    assert row["totalTokens"] is row["estimatedCostCny"] is None
    assert row["knownTokenLowerBound"] == 110 and row["knownCostLowerBoundCny"] > 0
    assert row["failedCalls"] + row["inFlightCalls"] == 1 and not row["reportedSuccess"]


def test_missing_cache_split_makes_cost_unknown_but_keeps_known_tokens(sample):
    root, groups = sample
    identifier = f"{audit.CASE}-r1-single"
    rows = groups["single"]
    rows[0]["usage"]["cache_read_tokens"] = None
    write_requests(root, identifier, rows)
    meter(root, identifier, rows)
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]
    row = result["trials"][1]
    assert row["totalTokens"] == 110 and row["estimatedCostCny"] is None
    assert not row["cacheUsageComplete"] and row["knownCostLowerBoundCny"] == pytest.approx(.000051)


@pytest.mark.parametrize("field,value,expected", [
    ("max_model_calls", 40, "transport_config_max_model_calls"),
    ("max_total_tokens", 500000, "transport_config_max_total_tokens"),
    ("deadline_epoch", 1900, "transport_config_deadline_epoch"),
    ("request_timeout_seconds", 300, "transport_config_request_timeout_seconds"),
])
def test_old_finite_runtime_cap_cannot_hide_behind_null_manifest(sample, field, value, expected):
    root, _ = sample
    change(root, f"private/{audit.CASE}-r1-single/transport-config.json", lambda row: row.update({field: value}))
    result = audit.audit_directory(root)
    assert not result["valid"] and expected in codes(result)


@pytest.mark.parametrize("kind", ["baseline", "cleanup", "counter", "forged_ack", "model", "single_task", "deadline", "order"])
def test_real_metadata_counterexamples_reject_false_comparability(sample, kind):
    root, groups = sample
    target = f"{audit.CASE}-r1-explicit"
    expected = None
    if kind == "baseline":
        change(root, f"private/{target}/baseline-files.json", lambda row: row.update({"candidate.py": ["file", "0"*64]}))
        expected = "baseline_not_original"
    elif kind == "cleanup":
        change(root, f"private/{target}/process-cleanup.json", lambda row: row.update(survivorPids=[123]))
        expected = "cleanup_incomplete"
    elif kind == "counter":
        change(root, f"trials/{target}.json", lambda row: row.update(modelCalls=2))
        expected = "row_metering_modelCalls"
    elif kind == "forged_ack":
        change(root, f"private/{target}/collaboration.json", lambda row: row.update(deliveredIds=[]))
        expected = "row_collaboration_mismatch"
    elif kind == "order":
        change(root, f"private/{audit.CASE}-r1-auto/execution-start.json", lambda row: row.update(epoch=110))
        expected = "trial_order_or_overlap"
    else:
        arm = "single" if kind == "single_task" else "explicit"
        rows = groups[arm]
        rows[0].update({"model": "another-model"} if kind == "model" else
                       {"toolNames": ["task"]} if kind == "single_task" else {"requestDeadlineEpoch": 500})
        write_requests(root, f"{audit.CASE}-r1-{arm}", rows)
        expected = {"model": "physical_model_route_drift", "single_task": "single_delegation_authority", "deadline": "physical_request_policy_drift"}[kind]
    result = audit.audit_directory(root)
    assert not result["valid"] and expected in codes(result)


@pytest.mark.parametrize("kind", ["missing", "extra", "rerun", "missing_baseline_anchor", "archive"])
def test_missing_or_replaced_cells_and_unbound_original_bytes_fail_closed(sample, kind):
    root, _ = sample
    if kind == "missing":
        (root / "trials" / f"{audit.CASE}-r1-single.json").unlink()
    elif kind == "extra":
        put(root, "trials/extra-best-of.json", {})
    elif kind == "rerun":
        change(root, "manifest.json", lambda row: row["schedule"][0].update(repeat=2))
    elif kind == "missing_baseline_anchor":
        change(root, "manifest.json", lambda row: row.pop("baselineFilesSha256"))
    else:
        with (root / "frozen-code.zip").open("ab") as stream:
            stream.write(b"tampered")
    result = audit.audit_directory(root)
    assert not result["valid"]
    assert len(result["trials"]) == 3, "Missing or failed cells must remain visible rather than selection-filtered."


def test_public_success_cannot_stand_in_for_external_acceptance(sample):
    root, _ = sample
    change(root, f"trials/{audit.CASE}-r1-auto.json", lambda row: row.update(oraclePassed=False, success=False))
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]
    assert result["trials"][2]["publicChecksPassed"]
    assert not result["trials"][2]["externalAcceptancePassed"]
    assert not result["trials"][2]["reportedSuccess"]


@pytest.mark.parametrize("field", ["price", "judgementPolicy", "requestPolicy"])
def test_corrupt_manifest_object_is_reported_without_crashing_or_dropping_cells(sample, field):
    root, _ = sample
    change(root, "manifest.json", lambda row: row.update({field: []}))
    result = audit.audit_directory(root)
    assert not result["valid"]
    assert len(result["trials"]) == 3


def test_non_json_tool_names_are_invalid_metadata_not_a_reader_crash(sample):
    root, groups = sample
    groups["single"][0]["toolNames"] = "broken-metadata"
    write_requests(root, f"{audit.CASE}-r1-single", groups["single"])
    result = audit.audit_directory(root)
    assert not result["valid"]
    assert "physical_tools_missing" in codes(result)


def test_unacknowledged_real_child_is_reported_as_delegation_not_collaboration_success(sample):
    root, groups = sample
    identifier = f"{audit.CASE}-r1-auto"
    groups["auto"].append(_request(2, "failed-child", "executor"))
    groups["auto"][-1].update(status="failed", usage={"source": "unknown"})
    put(root, f"private/{identifier}/collaboration.json", {
        "runs": [{"subagentId": "failed-child", "role": "executor", "returned": False}],
        "deliveredIds": [],
    })
    write_requests(root, identifier, groups["auto"])
    meter(root, identifier, groups["auto"])
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]
    row = result["trials"][2]
    assert row["actualDelegationObserved"] and row["physicalLeafRuns"] == 1
    assert not row["collaborationProtocolSatisfied"] and row["ownerAcknowledgedLeafResults"] == 0
    assert row["unknownUsageCalls"] == 1 and row["totalTokens"] is None


def test_missing_collaboration_record_cannot_hide_a_physical_child(sample):
    root, groups = sample
    identifier = f"{audit.CASE}-r1-auto"
    groups["auto"].append(_request(2, "unrecorded-child", "executor"))
    write_requests(root, identifier, groups["auto"])
    meter(root, identifier, groups["auto"])
    result = audit.audit_directory(root)
    assert not result["valid"]
    assert "physical_child_missing_collaboration_record" in codes(result)
    assert result["trials"][2]["actualDelegationObserved"]
    assert result["trials"][2]["physicalLeafRuns"] == 1


def test_changed_baseline_in_all_cells_cannot_replace_the_prepared_source_anchor(sample):
    root, _ = sample
    baseline = {"candidate.py": ["file", "0" * 64]}
    for arm in audit.ORDER:
        put(root, f"private/{audit.CASE}-r1-{arm}/baseline-files.json", baseline)
    change(root, "manifest.json", lambda row: row.update(baselineFilesSha256=audit.digest(audit.canonical(baseline))))
    result = audit.audit_directory(root)
    assert not result["valid"]
    assert "provenance_baselineFilesSha256" in codes(result)


def host_events(root, arm, count, extra=()):
    prefix = root / "private" / f"{audit.CASE}-r1-{arm}"
    path = prefix / "journal/dashboard/workspaces/ws_public/runs/run_public/events.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    types = ["run.queued", "run.started"] + [item for _ in range(count) for item in ("model.started", "model.completed")]
    rows = [{"sequence": i+1, "runId": "run_public", "type": name, "payload": {}}
            for i, name in enumerate(types)]
    rows.extend({"sequence": len(rows)+1, "runId": "run_public", **item} for item in extra)
    rows.append({"sequence": len(rows)+1, "runId": "run_public", "type": "run.completed", "payload": {}})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


@pytest.mark.parametrize("arm", ["single", "auto"])
def test_no_delegation_with_complete_transport_and_host_events_may_omit_lazy_artifact(sample, arm):
    root, groups = sample
    identifier = f"{audit.CASE}-r1-{arm}"
    (root / f"private/{identifier}/collaboration.json").unlink()
    host_events(root, arm, len(groups[arm]))
    result = audit.audit_directory(root)
    assert result["valid"], result["issues"]
    row = next(row for row in result["trials"] if row["arm"] == arm)
    assert not row["actualDelegationObserved"] and row["physicalLeafRuns"] == 0
    assert row["delegationAttempts"] == 0 and row["reportedSuccess"]
    assert row["collaborationArtifactStatus"] == "absent_no_delegation_verified"


@pytest.mark.parametrize("kind", ["task", "nested-task", "physical-child", "child-role", "host-child", "host-task",
    "child-file", "missing-host", "gap", "model-count", "unknown-usage", "malformed-tools", "corrupt",
    "symlink", "missing-cleanup", "unfinished", "explicit"])
def test_missing_collaboration_is_not_waived_when_delegation_or_evidence_is_uncertain(sample, kind):
    root, groups = sample
    arm = "explicit" if kind == "explicit" else "auto"
    identifier = f"{audit.CASE}-r1-{arm}"
    prefix = root / f"private/{identifier}"
    collaboration = prefix / "collaboration.json"
    collaboration.unlink()
    path = host_events(root, arm, len(groups[arm]))
    rows = groups[arm]
    if kind == "task":
        rows[0]["toolCalls"] = [{"name": "task"}]
    elif kind == "nested-task":
        rows[0]["toolCalls"] = [{"name": "other", "function": {"name": "task"}}]
    elif kind == "physical-child":
        rows[0]["subagentId"] = "sub_actual"
    elif kind == "child-role":
        rows[0]["role"] = "executor"
    elif kind in {"host-child", "host-task"}:
        host_events(root, arm, len(rows), ({"type": "subagent.completed" if kind == "host-child" else "tool.started",
            "payload": {"subagentId": "sub_actual"} if kind == "host-child" else {"toolName": "task", "operationId": "task-1"}},))
    elif kind == "child-file":
        target = path.parent / "subagent-runs/sub_actual.jsonl"
        target.parent.mkdir()
        target.write_text("not read by auditor")
    elif kind == "missing-host":
        path.unlink()
    elif kind == "gap":
        entries = path.read_text().splitlines()
        path.write_text("\n".join(entries[:1]+entries[2:]) + "\n")
    elif kind == "model-count":
        host_events(root, arm, len(rows)+1)
    elif kind == "unknown-usage":
        rows[0]["usage"] = {"source": "unknown"}
    elif kind == "malformed-tools":
        rows[0].pop("toolCalls")
    elif kind == "corrupt":
        collaboration.write_text("invalid json")
    elif kind == "symlink":
        collaboration.symlink_to(root / "absent-outside")
    elif kind == "missing-cleanup":
        (prefix / "process-cleanup.json").unlink()
    elif kind == "unfinished":
        rows[0]["status"] = "started"
    write_requests(root, identifier, rows)
    meter(root, identifier, rows)
    result = audit.audit_directory(root)
    assert not result["valid"]
    issues = [i for i in result["issues"] if i.get("cell") == identifier]
    assert any(i["code"] == "missing_or_invalid_artifact" for i in issues), issues
    row = next(row for row in result["trials"] if row["arm"] == arm)
    assert row.get("collaborationArtifactStatus") != "absent_no_delegation_verified"
