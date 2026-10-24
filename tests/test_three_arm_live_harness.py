"""External judging and complete failure rows; no provider calls."""
import json
from copy import deepcopy
import socket
import time

import pytest

from scripts import run_three_arm_real as live


def fixture_trial(tmp_path, arm="single", oracle=None):
    trial = {"id": f"fixture-r1-{arm}", "case": "fixture", "repeat": 1, "arm": arm}
    root = tmp_path / "private" / trial["id"]
    root.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "value.py").write_text("VALUE = 1\n")
    live.write_json(root / "workspace.json", {"path": str(workspace)})
    live.write_json(root / "baseline-files.json", live.workspace_files(workspace))
    live.write_json(root / "execution-start.json", {"epoch": time.time() - 3})
    live.write_json(tmp_path / "tasks.json", {"cases": [{
        "id": "fixture", "category": "test", "authorized_paths": ["value.py"],
        "oracle_source": oracle or 'import pathlib, sys\nassert (pathlib.Path(sys.argv[1]) / "value.py").read_text() == "VALUE = 2\\n"\n',
    }]})
    return trial, root, workspace


def test_hard_stop_still_judges_delivered_code_without_inventing_usage(tmp_path):
    trial, _, workspace = fixture_trial(tmp_path)
    (workspace / "value.py").write_text("VALUE = 2\n")
    row = live.finalize_trial(tmp_path, trial, process_code=-9)
    assert row["success"] and row["oracleSuccess"] and row["scopePassed"]
    assert row["errorClass"] == "HardTimeout"
    assert row["durationSeconds"] >= 3
    assert row["changedFiles"] == ["value.py"]
    assert not row["usageComplete"] and row["estimatedCostCny"] is None
    assert row["modelCalls"] == 0


def test_explicit_oracle_success_does_not_replace_actual_collaboration(tmp_path):
    trial, _, workspace = fixture_trial(tmp_path, "explicit")
    (workspace / "value.py").write_text("VALUE = 2\n")
    row = live.finalize_trial(tmp_path, trial, process_code=-9)
    assert row["oracleSuccess"] and not row["success"]
    assert not row["collaborationProtocolSatisfied"]
    assert row["actualLeafRuns"] == 0


def test_oracle_induced_out_of_scope_write_is_not_missed(tmp_path):
    oracle = 'import pathlib, sys\nw = pathlib.Path(sys.argv[1])\n(w / "forbidden.py").write_text("bad")\n'
    trial, _, _ = fixture_trial(tmp_path, oracle=oracle)
    row = live.finalize_trial(tmp_path, trial)
    assert row["oracleSuccess"] and not row["scopePassed"] and not row["success"]
    assert row["unauthorizedChanges"] == row["verificationInducedChanges"] == ["forbidden.py"]


def test_guardian_deadline_does_not_include_external_judge(tmp_path):
    trial, root, workspace = fixture_trial(tmp_path)
    (workspace / "value.py").write_text("VALUE = 2\n")
    live.write_json(root / "execution.json", {"durationSeconds": 2.25, "errorClass": None})
    row = live.finalize_trial(tmp_path, trial, process_seconds=4)
    assert row["durationSeconds"] == 2.25 and row["processSeconds"] == 4
    assert row["judgeSeconds"] > 0 and row["success"]


def test_failed_request_receipt_counts_and_missing_cache_blocks_exact_cost():
    snapshot = live.empty_transport_snapshot()
    snapshot.update(started=1, failed=1, admissionRejectedCount=2, usageComplete=True, inputTokensKnown=100,
                    outputTokensKnown=10, unknownUsageCalls=0,
                    requests=[{"event": "failed", "usage": {"source": "provider",
                        "input_tokens": 100, "output_tokens": 10, "cache_read_tokens": None}}])
    row = live.transport_metrics(snapshot)
    assert row["totalTokens"] == 110 and row["failedCalls"] == 1
    assert row['modelCalls'] == 1 and row['admissionRejectedCount'] == 2
    assert row["tokenUsageComplete"] and not row["usageComplete"]
    assert row["estimatedCostCny"] is None and row["knownCostLowerBoundCny"] > 0


def test_parent_tool_wrappers_cannot_modify_child_or_production_definitions():
    from minicode.tools import create_default_tool_registry
    owner = create_default_tool_registry(".", include_mcp=False, include_user_interaction=False)
    try:
        parent = live.independent_registry(owner, "explicit")
        child = live.independent_registry(owner, "explicit")
        original = owner.find("write_file").run
        parent.find("write_file").run = lambda *_: None
        assert child.find("write_file").run is original
        assert owner.find("write_file").run is original
        assert live.independent_registry(owner, "single").find("task") is None
    finally:
        owner.dispose()


def test_explicit_coordinator_has_only_dispatch_while_leaves_keep_coding_tools():
    from minicode.tools import create_default_tool_registry
    owner = create_default_tool_registry(".", include_mcp=False, include_user_interaction=False)
    try:
        parent = live.coordinator_registry(live.independent_registry(owner, "explicit"), "explicit")
        assert set(parent.list_all()) == {"task", "todo_write"}
        leaf = live.independent_registry(owner, "explicit")
        assert {"read_file", "grep_files", "write_file", "run_command"} <= set(leaf.list_all())
        for arm in ("single", "auto"):
            direct = live.coordinator_registry(live.independent_registry(owner, arm), arm)
            assert {"read_file", "grep_files", "write_file", "run_command"} <= set(direct.list_all())
    finally:
        owner.dispose()


def test_readonly_study_roles_cannot_execute_commands_or_write_source():
    from minicode.tools import create_default_tool_registry
    owner = create_default_tool_registry(".", include_mcp=False, include_user_interaction=False)
    try:
        for arm in ("single", "auto"):
            role = live.independent_registry(owner, arm, readonly=True)
            assert {"read_file", "grep_files"} <= set(role.list_all())
            assert not {"write_file", "edit_file", "run_command", "test_runner"} & set(role.list_all())
            assert (role.find("task") is not None) == (arm == "auto")
    finally:
        owner.dispose()


def test_unknown_route_price_retains_usage_without_inventing_currency(monkeypatch):
    monkeypatch.setattr(live, "PRICE", {"known": False})
    snapshot = live.empty_transport_snapshot()
    snapshot.update(started=1, completed=1, usageComplete=True, cacheUsageComplete=True,
                    inputTokensKnown=100, outputTokensKnown=10, cacheReadTokensKnown=20,
                    requests=[{"usage": {"source": "provider", "input_tokens": 100,
                                         "output_tokens": 10, "cache_read_tokens": 20}}])
    row = live.transport_metrics(snapshot)
    assert row["modelCalls"] == 1 and row["totalTokens"] == 110
    assert row["estimatedCostCny"] is None and row["knownCostLowerBoundCny"] is None
    assert row["estimatedCostUsd"] is None and not row["currencyPriceKnown"]


def test_registered_materialization_keeps_private_oracle_out_of_public_files(tmp_path):
    source = tmp_path / "source/minicode"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    oracle = tmp_path / "hidden-oracle.py"
    oracle.write_text("assert True\n")
    plan = tmp_path / "plan.md"
    plan.write_text("registered plan")
    case = {"id": "case", "category": "read", "prompt": "Inspect the module.",
            "authorized_paths": [], "baselineSourceDirectory": str(source.parent),
            "sourceInventories": {"baseline": {"minicode/__init__.py": live.sha(b"")}},
            "acceptance": {"oraclePath": str(oracle), "oracleSha256": live.sha(oracle.read_bytes())}}
    design = {"arms": ["single", "auto"], "runtime": {"model": "glm-5.3-flash"},
              "repeats": 1, "cases": [case], "schedule": [
                  {"id": "case-" + arm, "case": "case", "arm": arm, "repeat": 1}
                  for arm in ("single", "auto")]}
    path = tmp_path / "design.json"
    live.write_json(path, design)
    output = tmp_path / "prepared"
    live.prepare_study(output, path, plan)
    prepared = live.read_json(output / "tasks.json")["cases"][0]
    assert prepared["files"] == {"minicode/__init__.py": ""}
    assert str(oracle) not in prepared["prompt"]
    assert prepared["oracle_source"] == "assert True\n"
    assert prepared["verification_argv"] == []
    with pytest.raises(ValueError, match="never replace"):
        live.prepare_study(output, path, plan)
    (source / "__init__.py").write_text("changed")
    with pytest.raises(ValueError, match="baseline source hash mismatch"):
        live.prepare_study(tmp_path / "bad-source", path, plan)


@pytest.mark.parametrize("kind,regression,success", [("read", False, True), ("write", False, False), ("write", True, True)])
def test_registered_study_uses_private_copy_and_requires_real_write_regression(
    tmp_path, monkeypatch, kind, regression, success,
):
    monkeypatch.setattr(live, "STUDY", {"limits": {"externalJudgeSeconds": 20}})
    monkeypatch.setattr(live, "PRICE", {"known": False})
    trial, root, workspace = fixture_trial(tmp_path)
    (workspace / "minicode").mkdir()
    (workspace / "minicode/__init__.py").write_text("")
    target = workspace / "minicode/value.py"
    target.write_text("VALUE = 1\n")
    live.write_json(root / "baseline-files.json", live.workspace_files(workspace))
    if kind == "write":
        target.write_text("VALUE = 2\n")
    if regression:
        (workspace / "tests").mkdir()
        (workspace / "tests/test_eval_regression.py").write_text("from minicode.value import VALUE\ndef test_value(): assert VALUE == 2\n")
    oracle = 'import pathlib, sys\nw=pathlib.Path(sys.argv[1])\n(w / "judge-only.txt").write_text("private")\n'
    live.write_json(tmp_path / "tasks.json", {"cases": [{
        "id": "fixture", "category": kind, "authorized_paths": [] if kind == "read" else
        ["minicode/value.py", "tests/test_eval_regression.py"], "oracle_source": oracle,
        "generatedRegressionRequired": kind != "read",
    }]})
    row = live.finalize_trial(tmp_path, trial)
    assert row["success"] is success
    assert row["oracleSuccess"] and not (workspace / "judge-only.txt").exists()
    assert row["authorizedSourceChanged"] == (kind == "write")
    assert row["totalTokens"] is None and row["knownTotalTokens"] == 0
    assert row["interventions"]["humanApprovalCount"] is None
    if kind == "read":
        assert row["regressionTest"]["status"] == "not_required_read_only"


def test_journal_permission_counts_do_not_claim_human_approvals(tmp_path):
    path = tmp_path / "journal/run/events.ndjson"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps({"type": kind}) for kind in
                              ["permission.requested", "permission.decided", "permission.decided"]))
    metrics = live.intervention_metrics(tmp_path)
    assert metrics["permissionRequestEvents"] == 1 and metrics["permissionDecisionEvents"] == 2
    assert metrics["humanApprovalCount"] is None and metrics["humanCorrectionCount"] is None


def test_study_configuration_removes_inherited_caps_and_heterogeneous_models(tmp_path, monkeypatch):
    from minicode.agent_budget import AgentTurnBudget
    from minicode import config
    for name in ("STUDY", "LIMITS", "PRICE", "DEFAULT", "HARNESS_FILES"):
        monkeypatch.setattr(live, name, getattr(live, name))
    limits = {**live.LIMITS, "maxTokens": None, "maxModelCalls": None, "maxCostUsd": None}
    live.write_json(tmp_path / "study-design.json", {"arms": ["single", "auto"], "limits": limits,
                    "runtime": {"model": "glm-5.3-flash", "maxOutputTokens": 8192}})
    monkeypatch.setattr(config, "load_runtime_config", lambda *_: {
        "model": "glm-5.3-flash", "subagentModels": {"general": "different-model"},
        "agentTurnBudgetTokens": 100, "agentTurnBudgetModelCalls": 1, "agentTurnBudgetCostUsd": "1"})
    live.configure_study(tmp_path)
    runtime = live.frozen_runtime()
    budget = AgentTurnBudget.from_runtime(runtime).snapshot()
    assert budget.limit_total_tokens is None and budget.limit_model_calls is None and budget.limit_cost_usd is None
    assert runtime["subagentModels"] == {} and runtime["subagentRoutingEnabled"] is False
    assert live.PRICE["known"] is False


def test_private_source_drift_is_rejected_before_runtime(tmp_path, monkeypatch):
    dependency = tmp_path / "oracle-support/case/0/parent.py"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("VALUE = 1\n")
    case = {"id": "case", "category": "read", "prompt": "Inspect.", "authorized_paths": [],
            "sourceInventories": {"baseline": {}}, "acceptance": {"oracleSha256": live.sha(b"pass\n")},
            "oracleDependencyDirectories": {"/registered-parent": {"parent.py": live.sha(dependency.read_bytes())}}}
    design = {"cases": [case]}
    monkeypatch.setattr(live, "STUDY", design)
    live.write_json(tmp_path / "study-design.json", design)
    live.write_json(tmp_path / "tasks.json", {"cases": [{**case, "files": {}, "oracle_source": "pass\n",
        "generatedRegressionRequired": False, "verification_argv": []}]})
    live.write_json(tmp_path / "oracle-support-hashes.json", {"oracle-support/case/0/parent.py": live.sha(dependency.read_bytes())})
    manifest = {"studyDesignSha256": live.sha((tmp_path / "study-design.json").read_bytes()),
                "oracleSupportSha256": live.sha((tmp_path / "oracle-support-hashes.json").read_bytes())}
    live.validate_study_files(tmp_path, manifest)
    dependency.write_text("VALUE = 2\n")
    with pytest.raises(RuntimeError, match="private oracle source changed"):
        live.validate_study_files(tmp_path, manifest)


@pytest.mark.parametrize("arm", ("single", "auto"))
def test_registered_runtime_offline_parent_child_model_and_unlimited_budget(tmp_path, monkeypatch, arm):
    """Execute the real runtime/dispatcher; only provider responses are replaced."""
    from minicode import model_registry
    from minicode import openai_adapter

    runtime = {"model": "glm-5.3-flash", "provider": "custom",
               "customBaseUrl": "https://offline.example.invalid/v1", "customApiKey": "offline-fixture",
               "subagentRoutingEnabled": False, "subagentModels": {}, "temperature": 0,
               "modelMaxRetries": 0, "maxOutputTokens": 8192,
               "agentTurnBudget": {"maxTokens": None, "maxModelCalls": None, "maxCostUsd": None},
               "memoryHybridEnabled": False, "reflectionSynthesizerMode": "rule",
               "intentSemanticClassifier": {"enabled": False}, "skillCandidateReranker": {"enabled": False}}
    limits = {**live.LIMITS, "maxTokens": None, "maxModelCalls": None, "maxCostUsd": None}
    monkeypatch.setattr(live, "STUDY", {"limits": {"externalJudgeSeconds": 20}})
    monkeypatch.setattr(live, "LIMITS", limits)
    monkeypatch.setattr(live, "PRICE", {"known": False})
    monkeypatch.setattr(live, "frozen_runtime", lambda: deepcopy(runtime))
    monkeypatch.setattr(live, "validate_study_files", lambda *_: None)
    monkeypatch.setenv("MINI_CODE_MODEL_MODE", "real")
    route = live.resolved_transport_route(runtime)
    adapters, calls = [], []
    original = model_registry.create_model_adapter

    def factory(*args, **kwargs):
        adapter = original(*args, **kwargs)
        adapters.append(adapter)
        return adapter

    class OfflineStream:
        status = 200
        headers = {"x-request-id": "offline-fixture"}

        def __init__(self, event):
            self.chunks = iter([b"data: " + json.dumps(event).encode() + b"\n\n", b"data: [DONE]\n\n"])

        def read1(self, _amount):
            return next(self.chunks, b"")

        def close(self):
            pass

    def open_http(request, *, timeout):
        first = not calls
        calls.append(json.loads(request.data))
        usage = {"prompt_tokens": 11, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 0}}
        if arm == "auto" and first:
            choice = {"delta": {"tool_calls": [{"index": 0, "id": "offline-task", "function": {
                "name": "task", "arguments": json.dumps({"description": "inspect fixture",
                    "prompt": "Read minicode/value.py and summarize.", "agent_type": "explore"})}}]},
                "finish_reason": "tool_calls"}
        else:
            choice = {"delta": {"content": "<final>Offline fixture complete."}, "finish_reason": "stop"}
        return OfflineStream({"choices": [choice], "usage": usage})

    monkeypatch.setattr(model_registry, "create_model_adapter", factory)
    monkeypatch.setattr(openai_adapter, "open_verified_url", open_http)
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("offline lifecycle attempted network"))
    trial = {"id": "offline-" + arm, "case": "offline", "repeat": 1, "arm": arm}
    live.write_json(tmp_path / "tasks.json", {"cases": [{"id": "offline", "category": "read",
        "files": {"minicode/__init__.py": "", "minicode/value.py": "VALUE = 1\n"},
        "prompt": "Inspect the source without changing files.", "authorized_paths": [], "verification_argv": []}]})
    live.write_json(tmp_path / "manifest.json", {"schedule": [trial], "routeSha256": live.route_hash(runtime),
        "productionSha256": live.production_sha(), "harnessSha256": live.harness_sha(), "limits": limits,
        "price": live.PRICE, "taskSha256": live.sha((tmp_path / "tasks.json").read_bytes()),
        "resolvedTransportRouteSha256": route, "roleTurnLimits": {"explore": 12, "plan": 8, "general": 15}})
    live.execute_trial(tmp_path, trial["id"])
    root = tmp_path / "private" / trial["id"]
    execution = live.read_json(root / "execution.json")
    assert execution["errorClass"] is None, live.read_json(root / "error.json") if (root / "error.json").exists() else execution
    assert adapters and calls
    assert {a.runtime["model"] for a in adapters} == {"glm-5.3-flash"}
    assert len(adapters) >= (2 if arm == "auto" else 1)
    assert {c["model"] for c in calls} == {"glm-5.3-flash"}
    assert "Offline fixture complete" in (root / "response.txt").read_text()
    ledger = live.SharedLedger(live.TransportConfig(**live.read_json(root / "transport-config.json"))).snapshot()
    assert ledger["started"] == len(calls) and ledger["completed"] == len(calls)
    assert ledger["inputTokensKnown"] == 11 * len(calls) and ledger["outputTokensKnown"] == 7 * len(calls)
    assert all(execution["budget"][name] is None for name in ("limit_total_tokens", "limit_model_calls", "limit_cost_usd"))
    contract = live.read_json(root / "runtime-contract.json")
    assert not {"run_command", "write_file", "edit_file", "test_runner"} & set(contract["tools"])
    assert live.intervention_metrics(root)["journalAvailable"]
    workspace = live.read_json(root / "workspace.json")["path"]
    before = {p: tuple(value) for p, value in live.read_json(root / "baseline-files.json").items()}
    assert live.changed_paths(before, live.workspace_files(live.Path(workspace))) == []
