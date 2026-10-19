"""Offline protocol/gating and shared-runner integration; no provider calls."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts import run_implementation_progress_live as live


def evidence(*, analyst=False, external=False):
    workflow = {
        "subagentId": "wf",
        "agentType": "workflow",
        "role": "workflow",
        "isContainer": True,
        "parentSubagentId": None,
        "startedEpoch": 1,
        "returnedEpoch": 8,
        "returned": True,
        "toolOk": False,
        "deliveredEpoch": 9,
        "deliveryEvidence": "task_result_owner_ack",
        "resultSha256": "a" * 64,
    }
    executor = {
        "subagentId": "exec",
        "agentType": "general",
        "role": "Executor",
        "isContainer": False,
        "parentSubagentId": "wf",
        "startedEpoch": 4,
        "returnedEpoch": 6,
        "returned": True,
        "toolOk": True,
        "projected": {"outcome": "completed"},
    }
    records, requests = (
        [workflow, executor],
        [{"subagentId": "exec", "event": "completed"}],
    )
    if analyst or external:
        records.append(
            {
                "subagentId": "analysis",
                "agentType": "plan",
                "role": "plan" if external else "Analyst",
                "isContainer": False,
                "parentSubagentId": None if external else "wf",
                "startedEpoch": 2,
                "returnedEpoch": 3,
                "returned": True,
                "deliveredEpoch": 3.5,
                "deliveryEvidence": "task_result_owner_ack",
                "resultSha256": "b" * 64,
            }
        )
        requests.append({"subagentId": "analysis", "event": "completed"})
    return records, requests, [], ["wf", "analysis"]


def test_optional_analysis_does_not_require_redundant_plan():
    for analyst in (False, True):
        result = live.collaboration_metrics(*evidence(analyst=analyst))
        assert result["collaborationProtocolSatisfied"]
        assert result["implementationLeafRuns"] == 1
        assert result["actualAnalystLeafRuns"] == int(analyst)
        assert result["deliveredAnalystLeafResults"] == int(analyst)
        assert result["readAnalysisLeafRuns"] == int(analyst)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_ack",
        "wrong_parent",
        "unreturned",
        "failed_response",
        "after_container",
        "early_ack",
    ],
)
def test_executor_request_or_foreign_receipt_cannot_prove_delivery(mutation):
    records, requests, messages, delivered = evidence()
    if mutation == "no_ack":
        records[0].pop("deliveryEvidence")
    elif mutation == "wrong_parent":
        records[1]["parentSubagentId"] = "other"
    elif mutation == "unreturned":
        records[1]["returned"] = False
    elif mutation == "failed_response":
        requests[0]["event"] = "failed"
    elif mutation == "after_container":
        records[1]["returnedEpoch"] = 10
    else:
        records[0]["deliveredEpoch"] = 7
    result = live.collaboration_metrics(records, requests, messages, delivered)
    assert not result["collaborationProtocolSatisfied"]
    assert (
        result["leafModelCalls"] == 1
    )  # Failed attempts remain physical resource use.


def test_external_plan_breaks_direct_recipe_even_with_delivered_executor():
    result = live.collaboration_metrics(*evidence(external=True))
    assert result["implementationLeafRuns"] == 1
    assert result["externalAnalysisLeafRuns"] == result["externalLeafRuns"] == 1
    assert not result["collaborationProtocolSatisfied"]


def test_container_with_no_real_leaf_response_never_counts_as_execution():
    records, _, messages, delivered = evidence()
    result = live.collaboration_metrics(
        records, [{"subagentId": "wf", "event": "completed"}], messages, delivered
    )
    assert result["actualLeafRuns"] == result["leafModelCalls"] == 0
    assert (
        not result["collaborationExecuted"]
        and not result["collaborationProtocolSatisfied"]
    )


def test_isolated_adapter_uses_successor_child_without_polluting_old_module(tmp_path):
    old_file = live.shared.__file__
    old_prompt = live.shared.prompt_for_arm
    old_harness = live.shared.HARNESS_FILES
    module = live.runner(tmp_path)
    assert module.DEFAULT == tmp_path
    assert Path(module.__file__).name == "run_implementation_progress_live.py"
    assert module.run_suite.__globals__["__file__"] == module.__file__
    assert module.execute_trial.__globals__["prompt_for_arm"] is live.prompt_for_arm
    assert live.shared.__file__ == old_file
    assert live.shared.prompt_for_arm is old_prompt
    assert live.shared.HARNESS_FILES == old_harness
    assert "第一步直接" in module.prompt_for_arm("task", "explicit")
    assert (
        module.prompt_for_arm("task", "single")
        == module.prompt_for_arm("task", "auto")
        == "task" + live.REGRESSION_REQUIREMENT
    )


@pytest.mark.parametrize("arm", live.FORMAL_ARMS)
@pytest.mark.parametrize("worker_raises", [False, True])
def test_trial_wrapper_binds_same_public_implementation_spec_and_restores_runtime(
    tmp_path, monkeypatch, arm, worker_raises,
):
    """Exercise wrapper binding; freeze validation has separate drift tests."""
    from minicode import agent_runtime, subagent_types
    from minicode.implementation_execution import ImplementationSpec

    selected = {"id": "selected-r1-" + arm, "case": "selected", "repeat": 1, "arm": arm}
    case = {
        "id": "selected", "authorized_paths": ["minicode/target.py", "tests/test_eval_regression.py"],
        "prompt": "Fix the public behavior and add a regression test.",
        "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py", "-k", "selected"],
        "oracle_source": "HIDDEN_ORACLE_MUST_NOT_ENTER_IMPLEMENTATION_CONTEXT",
    }
    role_limits, role_policy = {}, {"version": "test-frozen-role-policy"}
    monkeypatch.setattr(live, "role_turn_configuration", lambda: (role_limits, role_policy))
    live.shared.write_json(tmp_path / "manifest.json", {
        "schemaVersion": 5, "schedule": [selected], "roleTurnLimits": role_limits,
        "roleTurnPolicy": role_policy, "implementationCapabilities": live.IMPLEMENTATION_CAPABILITIES,
    })
    live.shared.write_json(tmp_path / "tasks.json", {"cases": [case]})
    _preflight_fixture({"cases": [case]}, tmp_path / "public-preflight")
    manifest = live.shared.read_json(tmp_path / "manifest.json")
    manifest.update(publicVerificationFixturePolicy=live.PUBLIC_FIXTURE_POLICY,
                    publicVerificationFixtures=live.public_fixture_manifest({"cases": [case]}),
                    publicPreflightSha256=live.shared.sha((tmp_path / "public-preflight/receipt.json").read_bytes()))
    live.shared.write_json(tmp_path / "manifest.json", manifest)
    received = []
    boundary_calls = []
    sentinel = object()

    def validated_manifest(output):
        assert output == tmp_path
        assert not boundary_calls and not received
        boundary_calls.append("validate")
        return manifest

    monkeypatch.setattr(live, "validate_successor", validated_manifest)

    def runtime_factory(*args, **kwargs):
        assert boundary_calls == ["validate", "execute"]
        boundary_calls.append("runtime")
        received.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(agent_runtime, "create_agent_turn_runtime", runtime_factory)
    old_role_reader = subagent_types._read_max_turns_override
    real_spec_loader = live.importlib.util.spec_from_file_location

    def load_shared_with_boundary(name, location):
        loaded = real_spec_loader(name, location)
        assert loaded is not None and loaded.loader is not None
        original_load = loaded.loader.exec_module

        def execute_at_runtime_boundary(output, trial_id):
            assert output == tmp_path and trial_id == selected["id"]
            assert boundary_calls == ["validate"]
            boundary_calls.append("execute")
            runtime = agent_runtime.create_agent_turn_runtime(
                workspace=tmp_path, prompt="unchanged parent prompt", deadline_monotonic=1234,
                include_mcp=False, allow_user_interaction=False,
            )
            if worker_raises:
                raise RuntimeError("downstream execution failed")
            return runtime

        def load(module):
            original_load(module)
            module.execute_trial = execute_at_runtime_boundary

        monkeypatch.setattr(loaded.loader, "exec_module", load)
        return loaded

    monkeypatch.setattr(live.importlib.util, "spec_from_file_location", load_shared_with_boundary)
    module = live.runner(tmp_path)
    if worker_raises:
        with pytest.raises(RuntimeError, match="downstream execution failed"):
            module.execute_trial(tmp_path, trial_id=selected["id"])
    else:
        assert module.execute_trial(tmp_path, selected["id"]) is sentinel
    assert agent_runtime.create_agent_turn_runtime is runtime_factory
    assert subagent_types._read_max_turns_override is old_role_reader
    assert boundary_calls == ["validate", "execute", "runtime"]
    assert len(received) == 1
    args, kwargs = received[0]
    spec = kwargs.pop("implementation_spec")
    assert args == ()
    assert kwargs == {"workspace": tmp_path, "prompt": "unchanged parent prompt",
                      "deadline_monotonic": 1234, "include_mcp": False, "allow_user_interaction": False}
    assert isinstance(spec, ImplementationSpec)
    assert spec.write_paths == tuple(case["authorized_paths"])
    assert spec.objective == case["prompt"]
    assert [(check.check_id, check.kind, check.argv, check.timeout_seconds) for check in spec.checks] == [
        ("public_existing", "tests", tuple(case["verification_argv"]), 120),
        ("public_regression", "tests", ("python3", "-m", "pytest", "-q", "tests/test_eval_regression.py"), 120),
    ]
    assert case["oracle_source"] not in repr(spec)


@pytest.mark.parametrize("changed", [None, "older-feedback-surface"])
def test_frozen_implementation_capability_drift_is_rejected(tmp_path, monkeypatch, changed):
    source, output = tmp_path / "history", tmp_path / "small"
    fixture_source(source)
    live.prepare(output, "engineering", source)
    offline_runtime(monkeypatch)
    live.freeze(output)
    manifest = live.shared.read_json(output / "manifest.json")
    if changed is None:
        manifest.pop("implementationCapabilities")
    else:
        manifest["implementationCapabilities"] = changed
    live.shared.write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="implementation capabilities changed"):
        live.validate_successor(output)


def fixture_source(path):
    path.mkdir()
    tasks = {
        "shared_files": {"minicode/a.py": "before", ".mini-code/agents/visual-judge.md":
                         (Path(__file__).parent / "fixtures/public_visual_judge.md").read_text()},
        "cases": [
            {
                "id": name,
                "files": {},
                "oracle_source": "independent",
                "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
                "prompt": name,
                "authorized_paths": ["minicode/a.py", "tests/test_eval_regression.py"],
            }
            for name in (*live.SMALL_CASES, *(f"other-{i}" for i in range(5)))
        ],
    }
    live.shared.write_json(path / "tasks.json", tasks)
    live.shared.write_json(
        path / "manifest.json",
        {"taskSha256": live.shared.sha((path / "tasks.json").read_bytes())},
    )
    return tasks


def test_prepare_keeps_selected_tasks_and_oracles_byte_identical(tmp_path):
    source, output = tmp_path / "historical", tmp_path / "new"
    before = fixture_source(source)
    digest = live.shared.sha((source / "tasks.json").read_bytes())
    live.prepare(output, "engineering", source)
    actual = live.shared.read_json(output / "tasks.json")
    assert actual["shared_files"] == before["shared_files"]
    assert actual["cases"] == before["cases"][:3]
    assert digest == live.shared.sha((source / "tasks.json").read_bytes())
    with pytest.raises(ValueError, match="new empty"):
        live.prepare(output, "engineering", source)


def test_prepare_rejects_changed_historical_tasks(tmp_path):
    source = tmp_path / "historical"
    fixture_source(source)
    (source / "tasks.json").write_text("{}")
    with pytest.raises(ValueError, match="frozen manifest"):
        live.prepare(tmp_path / "new", "engineering", source)


def test_fixed_small_and_formal_schedules_keep_every_cell_once():
    for phase, cases, arms, repeats in (
        ("engineering", list(live.SMALL_CASES), live.SMALL_ARMS, 1),
        (
            "formal",
            list(live.SMALL_CASES) + [f"other-{i}" for i in range(5)],
            live.FORMAL_ARMS,
            2,
        ),
    ):
        schedule = live.schedule_for(cases, phase)
        assert schedule == live.schedule_for(cases, phase)
        assert len(schedule) == len(cases) * len(arms) * repeats
        assert {(r["case"], r["repeat"], r["arm"]) for r in schedule} == {
            (c, r, a) for c in cases for r in range(1, repeats + 1) for a in arms
        }
        assert len({r["id"] for r in schedule}) == len(schedule)


def gate_fixture(path):
    path.mkdir()
    rows = []
    # Four successes: each arm has two, each case at least one.
    success_cells = {
        (live.SMALL_CASES[0], "single"),
        (live.SMALL_CASES[1], "single"),
        (live.SMALL_CASES[1], "explicit"),
        (live.SMALL_CASES[2], "explicit"),
    }
    for case in live.SMALL_CASES:
        for arm in live.SMALL_ARMS:
            rows.append(
                {
                    "case": case,
                    "repeat": 1,
                    "arm": arm,
                    "id": f"{case}-r1-{arm}",
                    "oracleSuccess": (case, arm) in success_cells,
                    "regressionTestPassed": True,
                    "scopePassed": True,
                    "processCleanup": {"cleanupComplete": True},
                    "usageComplete": True,
                    "unknownUsageCalls": 0,
                    "changedSourceFiles": ["minicode/a.py"],
                    "collaborationProtocolSatisfied": arm == "explicit",
                    "implementationLeafRuns": int(arm == "explicit"),
                }
            )
    live.shared.write_json(
        path / "manifest.json",
        {
            "schemaVersion": 5,
            "phase": "engineering",
            "protocolVersion": live.PROTOCOL_VERSION,
        },
    )
    live.shared.write_json(path / "results.json", {"trials": rows})
    audit = {"valid": True, "mode": "final", "auditedTrials": 6, "expectedTrials": 6}
    return rows, audit


def test_gate_accepts_predeclared_four_of_six_with_full_delivery(tmp_path):
    _, audit = gate_fixture(tmp_path / "small")
    result = live.gate(tmp_path / "small", audit=audit)
    assert result["passed"] and result["realRepairCount"] == 4
    assert result["repairsByArm"] == {"single": 2, "explicit": 2}


@pytest.mark.parametrize(
    "mutation,condition",
    [
        ("unknown_usage", "cleanScopeAndReceipts"),
        ("scope", "cleanScopeAndReceipts"),
        ("cleanup", "cleanScopeAndReceipts"),
        ("partial", "sixTerminalRows"),
        ("duplicate", "sixTerminalRows"),
        ("test_only", "atLeastFourRealRepairs"),
        ("regression_failed", "atLeastFourRealRepairs"),
        ("no_delivery", "allExplicitExecutorDelivered"),
        ("audit", "validFinalAudit"),
        ("unbalanced_arm", "eachArmAtLeastTwo"),
        ("uncovered_case", "eachCaseAtLeastOne"),
    ],
)
def test_gate_rejects_missing_integrity_or_coverage(tmp_path, mutation, condition):
    output = tmp_path / "small"
    rows, audit = gate_fixture(output)
    if mutation == "unknown_usage":
        rows[0]["unknownUsageCalls"] = 1
    elif mutation == "scope":
        rows[0]["scopePassed"] = False
    elif mutation == "cleanup":
        rows[0]["processCleanup"]["cleanupComplete"] = False
    elif mutation == "partial":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif mutation == "regression_failed":
        rows[0]["regressionTestPassed"] = False
    elif mutation == "test_only":
        rows[0]["changedSourceFiles"] = []
    elif mutation == "no_delivery":
        rows[1]["collaborationProtocolSatisfied"] = False
    elif mutation == "audit":
        audit["valid"] = False
    elif mutation == "unbalanced_arm":
        for row in rows:
            row["oracleSuccess"] = (
                row["arm"] == "single" or row["case"] == live.SMALL_CASES[0]
            )
    else:
        for row in rows:
            row["oracleSuccess"] = row["case"] != live.SMALL_CASES[2]
    live.shared.write_json(output / "results.json", {"trials": rows})
    result = live.gate(output, audit=audit)
    assert not result["passed"] and not result["conditions"][condition]


@pytest.mark.parametrize("change_source", [False, True])
def test_actual_shared_finalizer_does_not_promote_test_only_changes(
    tmp_path, change_source
):
    output = tmp_path / "new"
    trial = {"id": "fixture-r1-single", "case": "fixture", "repeat": 1, "arm": "single"}
    private = output / "private" / trial["id"]
    private.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    (workspace / "minicode").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "minicode/a.py").write_text("old")
    (workspace / "minicode/__init__.py").write_text("")
    live.shared.write_json(private / "workspace.json", {"path": str(workspace)})
    live.shared.write_json(
        private / "baseline-files.json", live.shared.workspace_files(workspace)
    )
    live.shared.write_json(
        output / "tasks.json",
        {
            "cases": [
                {
                    "id": "fixture",
                    "category": "test",
                    "authorized_paths": [
                        "minicode/a.py",
                        "tests/test_eval_regression.py",
                    ],
                    "oracle_source": "pass\n",
                }
            ]
        },
    )
    (workspace / "tests/test_eval_regression.py").write_text("pass\n")
    module = live.runner(output)
    if change_source:
        (workspace / "minicode/a.py").write_text("fixed")
    row = module.finalize_trial(output, trial)
    assert row["oracleSuccess"]
    assert row["changedSourceFiles"] == (["minicode/a.py"] if change_source else [])


def test_formal_freeze_refuses_failed_gate_before_archive_or_model(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "history", tmp_path / "formal"
    fixture_source(source)
    live.prepare(output, "formal", source)
    module = SimpleNamespace(frozen_runtime=lambda: {"model": "glm-5.3-flash"})
    monkeypatch.setattr(live, "runner", lambda _: module)
    monkeypatch.setattr(live, "gate", lambda _: {"passed": False})
    with pytest.raises(ValueError, match="engineering gate failed"):
        live.freeze(output, tmp_path / "failed-small")
    assert (
        not (output / "manifest.json").exists()
        and not (output / "frozen-code.zip").exists()
    )


def _preflight_fixture(document, output, *, runtime=None):
    """A completed host preflight input for freeze-binding tests, not execution proof."""
    import json
    report = {"preflightVersion": 1, "passed": True, "policy": live.PUBLIC_FIXTURE_POLICY,
        "fixtures": live.public_fixture_manifest(document),
        "taskDocumentSha256": live.shared.sha(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()),
        "cases": [{"case": c["id"], "argv": c["verification_argv"], "status": "passed",
                   "verification": {"status": "passed"}, "evidence": [{"argv": c["verification_argv"], "status": "passed", "exitCode": 0}]}
                  for c in document["cases"]]}
    live.shared.write_json(output / "receipt.json", report)
    return report


def offline_runtime(monkeypatch):
    monkeypatch.setattr(live, "run_public_preflight", _preflight_fixture)
    original = live.runner

    def isolated(output):
        module = original(output)
        module.frozen_runtime = lambda: {"model": "glm-5.3-flash"}
        module.route_hash = lambda _: "route"
        module.resolved_transport_route = lambda _: "resolved-route"
        return module

    monkeypatch.setattr(live, "runner", isolated)


def test_freeze_contains_all_current_production_including_new_progress_file(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "history", tmp_path / "small"
    fixture_source(source)
    live.prepare(output, "engineering", source)
    offline_runtime(monkeypatch)
    # The independent auditor is an explicit frozen input; it must exist before freezing.
    assert (live.ROOT / "scripts/audit_implementation_progress.py").exists()
    live.freeze(output)
    manifest = live.shared.read_json(output / "manifest.json")
    assert manifest["phase"] == "engineering" and len(manifest["schedule"]) == 6
    assert manifest["implementationCapabilities"] == live.IMPLEMENTATION_CAPABILITIES
    assert manifest["limits"] == live.shared.LIMITS
    assert manifest["model"] == "glm-5.3-flash"
    assert manifest["harnessFiles"] == list(live.SUCCESSOR_HARNESS_FILES)
    with zipfile.ZipFile(output / "frozen-code.zip") as archive:
        assert {
            p.relative_to(live.ROOT).as_posix()
            for p in (live.ROOT / "minicode").rglob("*.py")
        } <= set(archive.namelist())
        for name in live.SUCCESSOR_HARNESS_FILES:
            assert (
                archive.read("scripts/" + name)
                == (live.ROOT / "scripts" / name).read_bytes()
            )
    assert live.validate_successor(output) == manifest
    with pytest.raises(ValueError, match="Manifest exists"):
        live.freeze(output)
    (output / "analysis-plan.md").write_text("different")
    with pytest.raises(ValueError, match="plan changed"):
        live.validate_successor(output)


def test_freeze_rechecks_original_task_content_not_only_prepared_digest(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "history", tmp_path / "small"
    fixture_source(source)
    live.prepare(output, "engineering", source)
    task = live.shared.read_json(output / "tasks.json")
    task["cases"][0]["oracle_source"] = "tampered"
    live.shared.write_json(output / "tasks.json", task)
    provenance = live.shared.read_json(output / "source-provenance.json")
    provenance["preparedTaskSha256"] = live.shared.sha(
        (output / "tasks.json").read_bytes()
    )
    live.shared.write_json(output / "source-provenance.json", provenance)
    with pytest.raises(ValueError, match="selected historical tasks"):
        live.freeze(output)


def test_formal_freeze_requires_same_runtime_that_passed_engineering(
    tmp_path, monkeypatch
):
    source, output, small = (
        tmp_path / "history",
        tmp_path / "formal",
        tmp_path / "small",
    )
    fixture_source(source)
    live.prepare(output, "formal", source)
    offline_runtime(monkeypatch)
    small.mkdir()
    live.shared.write_json(small / "manifest.json", {"productionSha256": "different"})
    monkeypatch.setattr(live, "gate", lambda _: {"passed": True})
    with pytest.raises(
        ValueError, match="engineering/formal contract differs: productionSha256"
    ):
        live.freeze(output, small)
    assert not (output / "manifest.json").exists()


def test_formal_role_limit_drift_is_rejected_before_any_execution(
    tmp_path, monkeypatch
):
    source, small, formal = (
        tmp_path / "history",
        tmp_path / "small",
        tmp_path / "formal",
    )
    fixture_source(source)
    live.prepare(small, "engineering", source)
    live.prepare(formal, "formal", source)
    offline_runtime(monkeypatch)
    live.freeze(small)
    monkeypatch.setattr(live, "gate", lambda _: {"passed": True})
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "99")
    with pytest.raises(
        ValueError, match="engineering/formal contract differs: roleTurnLimits"
    ):
        live.freeze(formal, small)
    assert not (formal / "manifest.json").exists()


def test_formal_freeze_requires_same_engineering_implementation_capabilities(tmp_path, monkeypatch):
    source, small, formal = tmp_path / "history", tmp_path / "small", tmp_path / "formal"
    fixture_source(source)
    live.prepare(small, "engineering", source)
    live.prepare(formal, "formal", source)
    offline_runtime(monkeypatch)
    live.freeze(small)
    manifest = live.shared.read_json(small / "manifest.json")
    manifest["implementationCapabilities"] = "older-feedback-surface"
    live.shared.write_json(small / "manifest.json", manifest)
    monkeypatch.setattr(live, "gate", lambda _: {"passed": True})
    with pytest.raises(ValueError, match="engineering/formal contract differs: implementationCapabilities"):
        live.freeze(formal, small)
    assert not (formal / "manifest.json").exists()
    assert not (formal / "frozen-code.zip").exists()
