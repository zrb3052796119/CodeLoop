"""External judging and complete failure rows; no provider calls."""
import time

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
