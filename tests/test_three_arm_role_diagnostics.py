"""Synthetic diagnostics only; no active experiment, model or oracle execution."""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import zipfile

import pytest

from scripts import diagnose_three_arm_real as diag
from test_three_arm_audit import audit, experiment, write


PRICE = {"perMillionInput": 0.8, "perMillionOutput": 2.8, "perMillionCacheRead": 0.23}

TASK_SOURCE = '''from minicode.multi_agent_contracts import _MAX_TEXT
_WORKFLOW_PROMPT_MAX_CHARS = _MAX_TEXT - 200
def _validate(input_data):
    agent_type = input_data.get("agent_type", "general")
    raw_prompt = input_data.get("prompt")
    prompt = raw_prompt.strip() if isinstance(raw_prompt, str) else ""
    if agent_type == "workflow" and len(prompt) > _WORKFLOW_PROMPT_MAX_CHARS:
        raise ValueError("error[workflow_prompt_oversized]: too long")
'''


def prompt_archive(tmp_path, task=TASK_SOURCE, contract="_MAX_TEXT = 4000\n"):
    path = tmp_path / "frozen-code.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("minicode/tools/task.py", task)
        bundle.writestr("minicode/multi_agent_contracts.py", contract)
    return path


def request(actor="main", role="main", event="completed", inputs=100, outputs=20, cache=40, **extra):
    return {
        "event": event, "role": role, "subagentId": None if actor == "main" else actor,
        "usage": {"source": "provider" if inputs is not None and outputs is not None else "unavailable",
                  "input_tokens": inputs, "output_tokens": outputs, "cache_read_tokens": cache},
        **extra,
    }


def fixture_audit(tmp_path):
    manifest, results = experiment(tmp_path, owner_ack=True, rejections=True)
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"], summary["issues"]
    write(tmp_path / "audit-summary.json", summary)
    return manifest, results, summary


def test_partial_receipt_components_and_failed_requests_contribute_only_known_cost():
    requests = [
        request(event="failed", seconds=9),
        request(event="started", inputs=50, outputs=None, cache=None, seconds=5),
        request(event="failed", inputs=None, outputs=5, cache=None),
    ]
    result = diag.request_metrics(requests, PRICE)
    assert result["physicalRequests"] == 3 and result["failedRequests"] == 2
    assert result["inFlightRequests"] == 1 and result["completedRequests"] == 0
    assert result["knownInputTokens"] == 150
    assert result["knownOutputTokens"] == 25 and result["knownTotalTokens"] == 175
    assert result["knownCacheReadTokens"] == 40  # Included in 150 input, never added twice.
    assert result["completeReceiptFraction"] == pytest.approx(1 / 3)
    assert result["apiEquivalentCostCny"] is None
    assert result["apiEquivalentCostLowerBoundCny"] == pytest.approx((60 * .8 + 40 * .23 + 25 * 2.8 + 50 * .23) / 1e6)
    assert result["requestElapsedSeconds"] == {"observedRequests": 2, "median": 7, "maximum": 9}
    assert "sum" not in json.dumps(result["requestElapsedSeconds"])


def test_known_tokens_missing_cache_use_minimum_rate_without_claiming_exact_cost():
    result = diag.request_metrics([request(cache=None)], PRICE)
    assert result["tokenReceiptRequests"] == 1 and result["completeReceiptRequests"] == 0
    assert result["cacheReceiptRequests"] == 0 and result["apiEquivalentCostCny"] is None
    assert result["apiEquivalentCostLowerBoundCny"] == pytest.approx((100 * .23 + 20 * 2.8) / 1e6)
    assert diag.request_metrics([], PRICE)["apiEquivalentCostCny"] == 0
    missing = diag.request_metrics([request(inputs=None, outputs=None, cache=None)], PRICE)
    assert missing["apiEquivalentCostCny"] is None and missing["completeReceiptFraction"] == 0


def test_workflow_delivery_attempts_completed_leaves_and_rejected_admissions_are_separate():
    runs = [
        {"subagentId": "plan1", "role": "plan", "returned": True, "startedEpoch": 1, "returnedEpoch": 2},
        {"subagentId": "flow1", "role": "workflow", "isContainer": True, "returned": True, "startedEpoch": 3, "returnedEpoch": 8},
        {"subagentId": "exec1", "role": "executor", "parentSubagentId": "flow1", "returned": True, "startedEpoch": 4, "returnedEpoch": 6},
        {"subagentId": "exec2", "role": "executor", "parentSubagentId": "flow1", "returned": False, "startedEpoch": 7},
        {"subagentId": "blocked", "role": "reviewer", "returned": False, "startedEpoch": 7},
    ]
    requests = [request("plan1", "plan"), request("exec1", "executor"), request("exec2", "executor", event="failed")]
    result = diag.role_details(requests, [{"role": "reviewer", "reservedTokens": 9000}],
                               {"runs": runs, "deliveredIds": ["plan1", "flow1"]}, [], owner_ack=True)
    assert result["plan"]["directlyDeliveredLeafResults"] == 1
    assert result["executor"]["admittedLeafRuns"] == 2
    assert result["executor"]["completedResponseLeafRuns"] == 1
    assert result["executor"]["deliveredViaWorkflowLeafResults"] == 1
    assert result["workflow"]["workflowContainers"] == 1
    assert result["workflow"]["deliveredWorkflowResults"] == 1
    assert result["reviewer"]["attemptedLeafRuns"] == 1
    assert result["reviewer"]["admittedLeafRuns"] == 0
    assert result["reviewer"]["admissionRejections"] == 1
    assert sum(v.get("actualLeafRuns", 0) for v in result.values()) == 2
    # Role detail only carries a refusal count, never usage or costs.
    assert "apiEquivalentCostCny" not in result["reviewer"]


def test_repeated_read_counts_use_trial_actor_and_exact_arguments_without_intent_inference():
    call = {"name": "read_file", "arguments": {"path": "module.py", "offset": 0}}
    requests = [request("p", "plan", toolCalls=[call, copy.deepcopy(call)]),
                request("e", "executor", toolCalls=[call]),
                request("e", "executor", toolCalls=[{"name": "read_file", "arguments": {"path": "module.py", "offset": 100}}])]
    first = diag.role_details(requests, [], {}, [], owner_ack=True)
    assert first["plan"]["requestedFileReads"] == 2
    assert first["plan"]["identicalReadRequestsWithinActor"] == 1
    assert first["executor"]["identicalReadRequestsWithinActor"] == 0
    assert first["executor"]["readRequestsWithPriorOtherActor"] == 1
    # A new trial must not inherit previous observations.
    assert diag.role_details(requests[:1], [], {}, [], owner_ack=True) == {"plan": first["plan"]}


def test_requires_default_48_and_existing_complete_valid_audit_before_reporting(tmp_path):
    fixture_audit(tmp_path)
    with pytest.raises(diag.DiagnosticError, match="48"):
        diag.diagnose(tmp_path)
    saved = json.loads((tmp_path / "audit-summary.json").read_text())
    saved["mode"] = "partial"
    write(tmp_path / "audit-summary.json", saved)
    with pytest.raises(diag.DiagnosticError, match="valid final audit"):
        diag.diagnose(tmp_path, expected_trials=3)
    assert not list(tmp_path.glob("role-diagnostics.*"))


def test_partial_results_fail_before_opening_request_ledger(tmp_path, monkeypatch):
    _, results, _ = fixture_audit(tmp_path)
    results["trials"].pop()
    write(tmp_path / "results.json", results)
    monkeypatch.setattr(diag, "read_ledger", lambda _: pytest.fail("must not inspect active/incomplete trial"))
    with pytest.raises(diag.DiagnosticError, match="terminal"):
        diag.diagnose(tmp_path, expected_trials=3)


def test_full_descriptive_report_preserves_failures_and_writes_only_diagnostic_artifacts(tmp_path):
    _, results, _ = fixture_audit(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = diag.diagnose(tmp_path, expected_trials=3)
    assert report["trialCount"] == 3 and len(report["trials"]) == len(results["trials"])
    assert all(not row["success"] for row in report["trials"])
    assert report["arms"]["explicit"]["originalSuccesses"] == 0
    assert report["arms"]["explicit"]["physicalRequests"] == 1
    assert report["trials"][0]["stopEvidence"][0]["code"] == "unknown"
    assert report["mode"] == "exploratory_descriptive"
    assert report["workflowPromptLimit"]["maxChars"] is None
    assert all(role["oversizedWorkflowPromptRequests"] is None
               for arm in report["arms"].values() for role in arm["roles"].values())
    diag.write_report(tmp_path, report)
    text = (tmp_path / "role-diagnostics.md").read_text()
    assert "不等于推理耗时" in text and "标准 API 等价估算" in text
    assert "保留全部失败样本" in text
    assert all((tmp_path / name).read_bytes() == content for name, content in before.items())
    added = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()} - {p.as_posix() for p in before}
    assert added == {"role-diagnostics.json", "role-diagnostics.md"}


def test_stale_valid_audit_is_not_used_after_sqlite_evidence_changes(tmp_path):
    _, results, _ = fixture_audit(tmp_path)
    path = tmp_path / "private" / results["trials"][0]["id"] / "transport.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE requests SET status='failed'")
    with pytest.raises(diag.DiagnosticError, match="Evidence changed"):
        diag.diagnose(tmp_path, expected_trials=3)


def test_default_entry_accepts_all_48_audited_terminal_rows_including_failures(tmp_path):
    seed, final = tmp_path / "seed", tmp_path / "full"
    seed.mkdir()
    final.mkdir()
    manifest, result = experiment(seed, owner_ack=True, rejections=True)
    shutil.copyfile(seed / "frozen-code.zip", final / "frozen-code.zip")
    schedule, rows, cases = [], [], []
    for task in range(8):
        case = f"synthetic{task}"
        cases.append({"id": case, "authorized_paths": ["module.py"]})
        for repeat in (1, 2):
            for old in result["trials"]:
                assignment = {"id": f"{case}-r{repeat}-{old['arm']}", "case": case,
                              "repeat": repeat, "arm": old["arm"]}
                row = {**old, **assignment}
                shutil.copytree(seed / "private" / old["id"], final / "private" / row["id"])
                write(final / "trials" / (row["id"] + ".json"), row)
                schedule.append(assignment)
                rows.append(row)
    write(final / "tasks.json", {"cases": cases})
    manifest.update(schedule=schedule, repeats=2,
                    taskSha256=diag.digest((final / "tasks.json").read_bytes()))
    write(final / "manifest.json", manifest)
    write(final / "results.json", {"manifestSha256": diag.digest((final / "manifest.json").read_bytes()),
                                    "expectedTrials": 48, "trials": rows})
    summary = audit.Audit(final).run()
    assert summary["valid"], summary["issues"]
    write(final / "audit-summary.json", summary)
    report = diag.diagnose(final)
    assert report["trialCount"] == 48
    assert [r["success"] for r in report["trials"]] == [r["success"] for r in rows]
    assert all(value["trials"] == 16 for value in report["arms"].values())
    assert sum(value["physicalRequests"] for value in report["arms"].values()) == 48


def test_frozen_prompt_limit_uses_verified_archive_expression_without_executing_it(tmp_path):
    # Importing this archive would raise; AST-only inspection must not execute it.
    source = 'raise RuntimeError("must never execute archive")\n' + TASK_SOURCE
    archive = prompt_archive(tmp_path, source, "_MAX_TEXT = 4900\n")
    limit = diag.workflow_prompt_limit(archive)
    assert limit["maxChars"] == 4700  # Not the current production version's limit.
    assert limit["verified"] and limit["normalization"] == "strip"
    assert limit["taskSourceSha256"] == diag.digest(source.encode())


@pytest.mark.parametrize("source,contract", [
    (TASK_SOURCE.replace("_MAX_TEXT - 200", "get_limit()"), "_MAX_TEXT = 4000\n"),
    (TASK_SOURCE, "_MAX_TEXT = 4000\n_MAX_TEXT = 5000\n"),
    (TASK_SOURCE, "_MAX_TEXT = compute()\n"),
    (TASK_SOURCE, "_MAX_TEXT = True\n"),
    (TASK_SOURCE.replace("len(prompt) >", "len(prompt) >="), "_MAX_TEXT = 4000\n"),
    (TASK_SOURCE.replace("raw_prompt.strip()", "raw_prompt"), "_MAX_TEXT = 4000\n"),
    (TASK_SOURCE.replace("raise ValueError", "print"), "_MAX_TEXT = 4000\n"),
    (TASK_SOURCE.replace("minicode.multi_agent_contracts", "other.contracts"), "_MAX_TEXT = 4000\n"),
    (TASK_SOURCE.replace("    if agent_type", '    prompt = "replacement"\n    if agent_type'), "_MAX_TEXT = 4000\n"),
    (TASK_SOURCE + "\n_WORKFLOW_PROMPT_MAX_CHARS = 9000\n", "_MAX_TEXT = 4000\n"),
])
def test_unknown_or_ambiguous_frozen_prompt_constraint_is_null(tmp_path, source, contract):
    result = diag.workflow_prompt_limit(prompt_archive(tmp_path, source, contract))
    assert result["maxChars"] is None and result["verified"] is False


def test_absent_or_invalid_archive_cannot_inherit_live_production_limit(tmp_path):
    assert diag.workflow_prompt_limit(tmp_path / "absent.zip")["maxChars"] is None
    archive = tmp_path / "invalid.zip"
    archive.write_bytes(b"not a zip")
    assert diag.workflow_prompt_limit(archive)["maxChars"] is None


def test_model_proposed_workflow_requests_use_stripped_unicode_characters_not_execution(tmp_path):
    limit = diag.workflow_prompt_limit(prompt_archive(tmp_path))
    calls = [
        {"name": "task", "arguments": {"agent_type": "plan", "prompt": "x" * 9000}},
        {"name": "task", "arguments": {"agent_type": "workflow", "prompt": "汉" * 3800 + "\n "}},
        {"name": "task", "arguments": {"agent_type": "workflow", "prompt": "汉" * 3801}},
        # This would fail another input check first: still a model-proposed
        # oversized request, not evidence that the oversize guard ran.
        {"name": "task", "arguments": {"agent_type": "workflow", "action": "poll", "prompt": "x" * 3801}},
        {"name": "task", "arguments": {"agent_type": "workflow", "prompt": None}},
        {"name": "task", "arguments": "malformed arguments"},
        {"name": "read_file", "arguments": {"prompt": "x" * 9000}},
    ]
    result = diag.workflow_request_counts([request(toolCalls=calls)], limit)
    assert result == {"requestedTaskCalls": 6, "requestedWorkflowCalls": 4,
                      "workflowPromptStringRequests": 3, "workflowPromptUnavailableRequests": 1,
                      "oversizedWorkflowPromptRequests": 2}
    unknown = diag.workflow_request_counts([request(toolCalls=calls)], {"maxChars": None, "verified": False})
    assert unknown["requestedWorkflowCalls"] == 4
    assert unknown["oversizedWorkflowPromptRequests"] is None
    assert "reject" not in " ".join(result).lower()


def test_workflow_request_diagnostics_reach_role_trial_arm_and_markdown_without_changing_success(tmp_path):
    manifest, results = experiment(tmp_path, owner_ack=True, rejections=True)
    archive = tmp_path / "frozen-code.zip"
    with zipfile.ZipFile(archive) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    files.update({"minicode/tools/task.py": TASK_SOURCE.encode(),
                  "minicode/multi_agent_contracts.py": b"_MAX_TEXT = 4000\n"})
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    manifest["frozenCodeArchiveSha256"] = diag.digest(archive.read_bytes())
    manifest["productionSha256"] = diag.digest(b"".join(
        name.encode() + b"\0" + files[name] for name in sorted(files) if name.startswith("minicode/") and name.endswith(".py")))
    write(tmp_path / "manifest.json", manifest)
    results["manifestSha256"] = diag.digest((tmp_path / "manifest.json").read_bytes())
    write(tmp_path / "results.json", results)
    # The diagnostic counter uses exactly the response request evidence; no
    # actual workflow container or rejection is invented for this synthetic call.
    trial = results["trials"][0]
    with sqlite3.connect(tmp_path / "private" / trial["id"] / "transport.sqlite") as db:
        data = json.loads(db.execute("SELECT data FROM requests WHERE call=1").fetchone()[0])
        data["role"] = "main"
        data["toolCalls"] = [{"name": "task", "arguments": {"agent_type": "workflow", "prompt": "x" * 3801}}]
        db.execute("UPDATE requests SET data=? WHERE call=1", (json.dumps(data),))
    summary = audit.Audit(tmp_path).run()
    assert summary["valid"], summary["issues"]
    write(tmp_path / "audit-summary.json", summary)
    report = diag.diagnose(tmp_path, expected_trials=3)
    role = report["trials"][0]["roles"]["main"]
    assert role["requestedTaskCalls"] == role["requestedWorkflowCalls"] == role["oversizedWorkflowPromptRequests"] == 1
    assert report["arms"][trial["arm"]]["oversizedWorkflowPromptRequests"] == 1
    assert report["arms"][trial["arm"]]["roles"]["main"]["oversizedWorkflowPromptRequests"] == 1
    assert [r["success"] for r in report["trials"]] == [r["success"] for r in results["trials"]]
    assert report["workflowPromptLimit"]["maxChars"] == 3800
    text = diag.markdown(report)
    assert "模型提出的超长 workflow 请求" in text
    assert "不等于实际工具执行次数或已确认的超长拒绝次数" in text
