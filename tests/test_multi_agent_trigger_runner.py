"""Offline trigger registration and actual transport/runtime counterexamples."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from scripts import run_multi_agent_trigger as trigger


def task_document() -> dict:
    return {
        "generation": "trigger-v1",
        "shared_files": {"minicode/__init__.py": "", "minicode/a.py": "VALUE = 1\n"},
        "cases": [{
            "id": name, "triggerExpectation": "positive" if i < 6 else "negative",
            "category": "synthetic implementation", "prompt": "Fix VALUE in minicode/a.py to equal 2.",
            "files": {"tests/test_public.py": "def test_public(): assert True\n"},
            "authorized_paths": ["minicode/a.py", "tests/test_eval_regression.py"],
            "verification_argv": ["python3", "-m", "pytest", "-q", "tests/test_public.py"],
            "oracle_source": "PRIVATE_ORACLE_" + name,
        } for i, name in enumerate(trigger.CASES)],
    }


def prepared(tmp_path: Path) -> Path:
    pack, output = tmp_path / "pack", tmp_path / "registration"
    pack.mkdir()
    tasks = task_document()
    trigger.shared.write_json(pack / "tasks.json", tasks)
    trigger.shared.write_json(pack / "reference-fixes.json", {"private": "reference fixes"})
    proof = [{"case": c["id"], "oracleSha256": trigger.shared.sha(c["oracle_source"].encode()),
              "publicTestSha256": trigger.shared.sha(c["files"]["tests/test_public.py"].encode()),
              **{name: {"returncode": code} for name, code in (
                  ("baselinePublic", 0), ("baselineOracle", 1),
                  ("referencePublic", 0), ("referenceOracle", 0))}}
             for c in tasks["cases"]]
    for row in proof:
        row["baselineOracle"]["stdout"] = json.dumps({"passed": False, "errors": ["target failure"]})
        row["referenceOracle"]["stdout"] = json.dumps({"passed": True, "errors": []})
    trigger.shared.write_json(pack / "offline-proof.json", proof)
    trigger.shared.write_json(pack / "source-manifest.json", {
        "schemaVersion": 1, "generation": "trigger-v1", "origin": "synthetic", "modelCalls": 0,
        **{key: trigger.shared.sha((pack / name).read_bytes()) for key, name in (
            ("taskSha256", "tasks.json"), ("offlineProofSha256", "offline-proof.json"),
            ("referenceFixesSha256", "reference-fixes.json"))},
    })
    trigger.prepare(output, pack / "tasks.json", materials=pack)
    return output


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    output = prepared(tmp_path)
    files = {"minicode/__init__.py": b"", "scripts/__init__.py": b"",
             "scripts/run_multi_agent_trigger.py": b"print('FROZEN_TRIGGER_HOST')\n",
             "pyproject.toml": b"[project]\nname='test-host'\n"}
    monkeypatch.setattr(trigger, "host_files", lambda: files)
    monkeypatch.setattr(trigger, "runner", lambda *a, **k: SimpleNamespace(
        frozen_runtime=lambda: {"model": trigger.MODEL}, route_hash=lambda _: "a" * 64,
        resolved_transport_route=lambda _: "b" * 64))

    def preflight(document, destination, **kwargs):
        assert len(document["cases"]) == 8 and kwargs["require_pytest_report"] is True
        trigger.shared.write_json(destination / "receipt.json", {"passed": True})
        return {"passed": True}

    def probe(root, _output):
        assert (root / "scripts/run_multi_agent_trigger.py").read_bytes() == files["scripts/run_multi_agent_trigger.py"]
        return {"model": trigger.MODEL, "routeSha256": "a" * 64,
                "resolvedTransportRouteSha256": "b" * 64,
                "productionSha256": "c" * 64, "harnessSha256": "d" * 64,
                "environment": {"python": "offline fixture"}}

    monkeypatch.setattr(trigger, "run_public_preflight", preflight)
    monkeypatch.setattr(trigger, "validate_public_preflight", lambda *a, **k: None)
    monkeypatch.setattr(trigger, "probe_host", probe)
    trigger.freeze(output)
    return output


def commit_registration(output: Path) -> str:
    repo = output.parent
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", output.name], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Offline Test", "-c",
                    "user.email=offline@example.test", "commit", "-qm", "fixture registration"], check=True)
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_eight_unique_auto_cells_have_an_independent_contract():
    old = deepcopy(trigger.foundation.static_contract())
    rows = trigger.schedule_for()
    assert rows == trigger.schedule_for() and len(rows) == len({r["id"] for r in rows}) == 8
    assert {(r["case"], r["arm"], r["repeat"]) for r in rows} == {(c, "auto", 1) for c in trigger.CASES}
    contract = trigger.static_contract()
    assert contract["phase"] == "trigger-gen2"
    assert contract["protocolVersion"] == "multi-agent-trigger-v1"
    assert contract["triggerExpectations"] == dict(zip(trigger.CASES, ["positive"] * 6 + ["negative"] * 2))
    assert contract["gatePolicy"]["triggerRateDenominator"] == 8
    assert trigger.foundation.static_contract() == old
    assert "single" not in trigger.ARMS and "explicit" not in trigger.ARMS


def test_prepare_keeps_labels_and_oracle_host_only_without_duplicating_tasks(tmp_path):
    output = prepared(tmp_path)
    assert (output / "tasks.json").read_bytes() == (tmp_path / "pack/tasks.json").read_bytes()
    assert (output / "materials/reference-fixes.json").is_file()
    assert not (output / "materials/tasks.json").exists()
    text = trigger.prompt_for_arm("PUBLIC", "auto")
    assert "PUBLIC" in text and "600" in text and "1800" not in text
    assert "PRIVATE_ORACLE" not in text and "positive" not in text and "negative" not in text
    assert trigger.foundation.progress.EXPLICIT_INSTRUCTION not in text
    with pytest.raises(ValueError, match="only the auto"):
        trigger.prompt_for_arm("PUBLIC", "explicit")


@pytest.mark.parametrize("mutation", ["gen2_case", "label", "generated_test", "escape", "filter"])
def test_prepare_rejects_protocol_or_scope_drift(tmp_path, mutation):
    document = task_document()
    case = document["cases"][0]
    if mutation == "gen2_case":
        case["id"] = trigger.foundation.CASES[0]
    elif mutation == "label":
        case["triggerExpectation"] = "negative"
    elif mutation == "generated_test":
        case["files"]["tests/test_eval_regression.py"] = "def test_done(): pass"
    elif mutation == "escape":
        case["files"]["../private.py"] = "escape"
    else:
        case["verification_argv"] += ["-k", "only_one"]
    trigger.shared.write_json(tmp_path / "tasks.json", document)
    with pytest.raises(ValueError):
        trigger.prepare(tmp_path / "out", tmp_path / "tasks.json")
    assert not (tmp_path / "out").exists()


def test_freeze_and_commit_guard_bind_new_contract_and_all_materials(frozen):
    manifest = trigger.validate(frozen)
    assert manifest["runner"] == "scripts/run_multi_agent_trigger.py"
    assert manifest["arms"] == ["auto"] and len(manifest["schedule"]) == 8
    assert "materials/reference-fixes.json" in manifest["registrationFiles"]
    commit = commit_registration(frozen)
    assert trigger.require_committed_registration(frozen, commit) == commit
    manifest["phase"] = "gen2"
    trigger.shared.write_json(frozen / "manifest.json", manifest)
    with pytest.raises(ValueError, match="contract"):
        trigger.require_committed_registration(frozen, commit)


@pytest.mark.parametrize("mode", ["run", "child"])
def test_run_and_child_require_commit_before_runtime_creation(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(trigger, "runner", lambda *a, **k: pytest.fail("paid runtime must remain untouched"))
    with pytest.raises(ValueError, match="preregistered-commit"):
        trigger.dispatch(tmp_path, mode, None)
    assert not (tmp_path / "private").exists()


def test_real_git_guard_rejects_bytes_changed_after_commit(frozen):
    commit = commit_registration(frozen)
    (frozen / "materials/reference-fixes.json").write_text("{}")
    with pytest.raises(ValueError, match="material changed"):
        trigger.require_committed_registration(frozen, commit)


def test_bootstrap_executes_new_archived_entrypoint_and_refuses_repeat(frozen, monkeypatch):
    commit = commit_registration(frozen)

    class ReplacedProcess(Exception):
        pass

    def execute(_binary, argv, env):
        assert argv[1:3] == ["-I", "-B"]
        assert "run_multi_agent_trigger.py" in argv[3]
        result = subprocess.run(argv, env=env, text=True, capture_output=True, check=True)
        assert result.stdout.strip() == "FROZEN_TRIGGER_HOST"
        raise ReplacedProcess

    monkeypatch.setattr(trigger.os, "execve", execute)
    with pytest.raises(ReplacedProcess):
        trigger.dispatch(frozen, "run", commit)
    first = trigger.schedule_for()[0]["id"]
    trigger.shared.write_json(frozen / "private" / first / "execution-host.json", {"started": True})
    with pytest.raises(ValueError, match="durable evidence"):
        trigger.dispatch(frozen, "run", commit)


def test_child_cannot_skip_schedule_or_load_gen2_entrypoint(frozen, monkeypatch):
    commit = commit_registration(frozen)
    with pytest.raises(ValueError, match="next frozen"):
        trigger.dispatch(frozen, "child", commit, trial=trigger.schedule_for()[1]["id"])
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: calls.append(argv))
    # Undo the fixture's runner stub only for argument construction; no spawn occurs.
    module = trigger._foundation()._original_runner(frozen, commit=commit)
    module.subprocess.Popen([sys.executable, str(trigger.ROOT / "scripts/run_multi_agent_trigger.py"), "child"])
    assert calls[0][1:3] == ["-I", "-B"]
    assert "run_multi_agent_trigger.py" in calls[0][3]
    assert calls[0][-4:] == ["--preregistered-commit", commit, "--execution-host", str(trigger.ROOT)]


def test_runtime_overrides_and_auto_registry_do_not_mutate_gen2(tmp_path, monkeypatch):
    from minicode import config
    from minicode.subagent_model_routing import resolve_subagent_model_route
    from minicode.tooling import ToolRegistry
    from minicode.tools import read_file, edit_file, task
    old = deepcopy(trigger.foundation.static_contract())
    monkeypatch.setattr(config, "load_runtime_config", lambda *_: {
        "model": "private-model", "temperature": 1, "subagentRoutingEnabled": True,
        "subagentModels": {"explore": "different"}, "modelMaxRetries": 9})
    module = trigger.runner(tmp_path)
    runtime = module.frozen_runtime()
    assert runtime["model"] == trigger.MODEL and runtime["temperature"] == runtime["modelMaxRetries"] == 0
    assert runtime["agentTurnBudget"] == {"maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5"}
    for role in trigger.ROLE_TURN_LIMITS:
        route = resolve_subagent_model_route(runtime, role)
        assert not route.enabled and route.model == trigger.MODEL
    registry = ToolRegistry([read_file.read_file_tool, edit_file.edit_file_tool, task.task_tool])
    selected = module.coordinator_registry(module.independent_registry(registry, "auto"), "auto")
    assert set(selected.list_all()) == {"read_file", "edit_file", "task"}
    assert module.EvaluationTransport is trigger.ParentRequestTransport
    assert module.CollaborationEvidence is trigger.TriggerCollaborationEvidence
    assert trigger.foundation.static_contract() == old


def test_role_zero_settings_shadow_private_caps_and_restore_environment(monkeypatch):
    import os
    from minicode.subagent_types import configured_max_turns
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "1")
    with trigger._foundation().role_overrides(trigger.static_contract()):
        assert all(configured_max_turns(role) is None for role in trigger.ROLE_TURN_LIMITS)
    assert os.environ["MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS"] == "1"


class ScriptedResponse:
    status = 200
    headers = {"x-request-id": "offline-fixture", "authorization": "NEVER_RECORD_HEADERS"}

    def __init__(self, delta):
        self.chunks = iter([
            b"data:" + json.dumps({"id": "offline-response", "choices": [{"delta": delta,
                "finish_reason": "tool_calls" if "tool_calls" in delta else "stop"}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 100,
                          "prompt_tokens_details": {"cached_tokens": 0}}}).encode() + b"\n\n",
            b"data: [DONE]\n\n",
        ])
        self.closed = threading.Event()

    def read1(self, _amount):
        return next(self.chunks, b"")

    def close(self):
        self.closed.set()


RUNTIME = {"model": trigger.MODEL, "openaiBaseUrl": "https://example.invalid/v1",
           "openaiApiKey": "OFFLINE_NOT_A_KEY", "temperature": 0, "modelMaxRetries": 0,
           "provider": "custom", "customBaseUrl": "https://example.invalid/v1",
           "customApiKey": "OFFLINE_NOT_A_KEY",
           "modelTimeoutSeconds": 300, "maxOutputTokens": 8192}


@pytest.mark.parametrize("failed", [False, True])
def test_capture_is_complete_and_bound_before_success_or_failed_send(tmp_path, monkeypatch, failed):
    from minicode import openai_adapter as adapter
    sent = []

    def fake_send(request, **kwargs):
        paths = list((tmp_path / "parent-requests").glob("*.json"))
        assert len(paths) == 1  # durable body exists before transport is reached
        record = json.loads(paths[0].read_text())
        assert record["body"] == json.loads(request.data)
        sent.append(record)
        if failed:
            raise OSError("offline transport failure")
        return ScriptedResponse({"content": "done"})

    monkeypatch.setattr(adapter, "open_verified_url", fake_send)
    cfg = trigger.transport.TransportConfig(ledger_path=str(tmp_path / "transport.sqlite"),
        expected_model=trigger.MODEL, expected_route_sha256=trigger.transport.route_fingerprint(RUNTIME),
        deadline_epoch=time.time() + 30)
    content = "[Coding progress checkpoint] " + "完整正文" * 25000
    with trigger.ParentRequestTransport(cfg) as observed:
        if failed:
            with pytest.raises(Exception):
                adapter.OpenAIModelAdapter(dict(RUNTIME), None).next([{"role": "user", "content": content}])
        else:
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next([{"role": "user", "content": content}])
        row = observed.ledger.snapshot()["requests"][0]
    record = sent[0]
    assert content in json.dumps(record, ensure_ascii=False)
    assert record["callId"] == row["call"] and record["actor"]["role"] == "main"
    assert record["bodySha256"] == trigger.shared.sha(trigger.canonical(record["body"]))
    assert row["parentRequestSha256"] == trigger.shared.sha((tmp_path / row["parentRequestPath"]).read_bytes())
    assert record["capturedAtEpoch"] >= row["startedEpoch"]
    assert "OFFLINE_NOT_A_KEY" not in json.dumps(record) and "NEVER_RECORD_HEADERS" not in json.dumps(record)
    assert row["event"] == ("failed" if failed else "completed")


def test_capture_failure_prevents_send_and_never_overwrites_evidence(tmp_path, monkeypatch):
    from minicode import openai_adapter as adapter
    directory = tmp_path / "parent-requests"
    directory.mkdir()
    (directory / "1.json").write_text("existing evidence")
    monkeypatch.setattr(adapter, "open_verified_url", lambda *_a, **_k: pytest.fail("capture failure must stop sending"))
    cfg = trigger.transport.TransportConfig(ledger_path=str(tmp_path / "transport.sqlite"),
        expected_model=trigger.MODEL, expected_route_sha256=trigger.transport.route_fingerprint(RUNTIME),
        deadline_epoch=time.time() + 30)
    with trigger.ParentRequestTransport(cfg) as observed:
        with pytest.raises(FileExistsError):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next([{"role": "user", "content": "test"}])
        assert observed.ledger.snapshot()["failed"] == 1
    assert (directory / "1.json").read_text() == "existing evidence"


@pytest.mark.parametrize("wrong_owner", [False, True])
def test_async_poll_is_not_delivery_and_real_owner_ack_is_required(tmp_path, wrong_owner):
    from minicode.subagent_lifecycle import AsyncSubagentLifecycle, SubagentLifecycleNotFound
    from minicode.tooling import ToolResult
    lifecycle = AsyncSubagentLifecycle()
    with trigger.TriggerCollaborationEvidence(tmp_path / "collaboration.json") as evidence:
        spawned = lifecycle.spawn(agent_type="explore", runner=lambda identity, cancel: ToolResult(ok=True, output="evidence"))
        identity = spawned["subagentId"]
        terminal = lifecycle.poll(identity, wait_seconds=1)
        assert terminal["terminal"]
        record = {"subagentId": identity, "agentType": "explore", "role": "explore",
                  "parentSubagentId": "wrong_owner" if wrong_owner else None,
                  "startedEpoch": time.time() - 1, "returnedEpoch": time.time(),
                  "returned": True, "toolOk": True, "isContainer": False,
                  "resultSha256": trigger.shared.sha(b"evidence")}
        evidence.records.append(record)
        calls = [{"subagentId": identity, "event": "completed"}]
        assert not trigger.collaboration_metrics([record], calls, [], evidence.delivered)["collaborationExecuted"]
        with pytest.raises(SubagentLifecycleNotFound):
            lifecycle.acknowledge_delivery(("foreign-id",))
        assert "deliveredEpoch" not in record
        lifecycle.acknowledge_delivery((identity,))
        metrics = trigger.collaboration_metrics([record], calls, [], evidence.delivered)
        assert metrics["collaborationExecuted"] is (not wrong_owner)
        if not wrong_owner:
            assert record["deliveryEvidence"] == "async_lifecycle_owner_ack"
            assert record["asyncDeliveryOwnerActorId"] == "main"
            assert record["asyncDeliverySnapshot"] == terminal
            assert record["deliveredEpoch"] >= record["returnedEpoch"]
        else:
            assert "deliveryEvidence" not in record
    lifecycle.shutdown()


@pytest.mark.parametrize("queued", [False, True])
def test_real_async_cancel_ack_remains_auditable_without_counting_adoption(tmp_path, queued):
    from minicode.subagent_lifecycle import AsyncSubagentLifecycle, SubagentWorkerCancelled
    from minicode.tooling import ToolResult
    from scripts import audit_multi_agent_trigger as audit

    lifecycle = AsyncSubagentLifecycle(max_workers=1)
    entered, release = threading.Event(), threading.Event()

    def running(identity, cancel):
        entered.set()
        if queued:
            assert release.wait(5)
            return ToolResult(ok=True, output="blocker complete")
        assert cancel.wait(5)
        raise SubagentWorkerCancelled()

    try:
        with trigger.TriggerCollaborationEvidence(tmp_path / "collaboration.json") as evidence:
            running_job = lifecycle.spawn(agent_type="explore", runner=running)
            assert entered.wait(2)
            identity = running_job["subagentId"]
            if queued:
                target = lifecycle.spawn(agent_type="explore", runner=lambda *_: pytest.fail("cancelled queued job must never execute"))
                identity = target["subagentId"]
                assert target["status"] == "queued"
            else:
                # This is the ordinary _run observer's interrupted-worker shape.
                evidence.records.append({"subagentId": identity, "agentType": "explore", "role": "explore",
                    "parentSubagentId": None, "startedEpoch": time.time(), "isContainer": False,
                    "returned": False, "errorClass": "SubagentWorkerCancelled"})
            lifecycle.cancel(identity)
            terminal = lifecycle.poll(identity, wait_seconds=1)
            assert terminal["status"] == "cancelled" and terminal["result"] is None
            if not queued:
                evidence.records[0]["returnedEpoch"] = time.time()
            lifecycle.acknowledge_delivery((identity,))
            lifecycle.acknowledge_delivery((identity,))  # real owner receipts are idempotent
            calls = [] if queued else [{"call": 1, "subagentId": identity, "actorId": identity,
                "agentType": "explore", "role": "explore", "event": "completed"}]
            metrics = trigger.collaboration_metrics(evidence.records, calls, [], evidence.delivered)
            assert metrics["collaborationExecuted"] is False
            assert metrics["deliveredLeafResults"] == 0
            assert metrics["actualLeafRuns"] == (0 if queued else 1)
            saved = trigger.shared.read_json(tmp_path / "collaboration.json")
            if queued:
                assert saved["runs"] == [] and len(saved["asyncTerminalAcks"]) == 1
                ack = saved["asyncTerminalAcks"][0]
                assert ack["snapshot"] == terminal
                assert "startedEpoch" not in ack and "returnedEpoch" not in ack
                assert audit.queued_cancel_ack_valid(ack, {identity}, [])
            else:
                assert saved["asyncTerminalAcks"] == []
                record = saved["runs"][0]
                assert audit.owner_ack_valid(record, {identity})
                assert not audit.delivery_valid(record, {identity})
            examiner = audit.Audit(tmp_path)
            examiner.parent_requests = lambda *_: {}
            examiner.collaboration(tmp_path, {"id": "cancel", **metrics}, calls, {})
            assert examiner.issues == []
            assert not examiner._trigger["deliveredTrigger"]
    finally:
        release.set()
        lifecycle.shutdown(timeout_seconds=1)


@pytest.mark.parametrize("mutation, adopted", [
    ("none", True), ("failed", True), ("truncated", True), ("cancelled_no_result", False),
    ("agent_type", False), ("status", False), ("output_hash", False),
    ("ok_type", False), ("ok_mismatch", False), ("output_type", False), ("truncated_type", False),
])
def test_async_result_normalization_matches_independent_audit_with_completed_physical_call(mutation, adopted):
    from scripts import audit_multi_agent_trigger as audit
    snapshot = {"subagentId": "child", "agentType": "explore", "terminal": True, "status": "completed",
                "result": {"ok": True, "output": "evidence", "truncated": False}}
    record = {"subagentId": "child", "agentType": "explore", "role": "explore", "parentSubagentId": None,
              "isContainer": False, "returned": True, "toolOk": True, "resultSha256": trigger.shared.sha(b"evidence"),
              "startedEpoch": 10, "returnedEpoch": 20, "deliveredEpoch": 21,
              "deliveryEvidence": "async_lifecycle_owner_ack", "asyncDeliveryOwnerActorId": "main",
              "asyncDeliverySnapshot": snapshot}
    if mutation == "failed":
        snapshot.update(status="failed")
        snapshot["result"]["ok"] = record["toolOk"] = False
    elif mutation == "truncated": snapshot["result"].update(output="evid...", truncated=True)
    elif mutation == "cancelled_no_result": snapshot.update(status="cancelled", result=None)
    elif mutation == "agent_type": snapshot["agentType"] = "plan"
    elif mutation == "status": snapshot["status"] = "running"
    elif mutation == "output_hash": snapshot["result"]["output"] = "different"
    elif mutation == "ok_type": snapshot["result"]["ok"] = 1
    elif mutation == "ok_mismatch": snapshot["result"]["ok"] = False
    elif mutation == "output_type": snapshot["result"]["output"] = 3
    elif mutation == "truncated_type": snapshot["result"]["truncated"] = 1
    record["asyncDeliverySnapshotSha256"] = trigger.shared.sha(trigger.canonical(snapshot))
    calls = [{"subagentId": "child", "event": "completed"}]
    assert audit.delivery_valid(record, {"child"}) is adopted
    assert trigger.collaboration_metrics([record], calls, [], {"child"})["collaborationExecuted"] is adopted


def test_full_runtime_direct_tools_and_inconclusive_native_review_preserve_every_request(tmp_path, monkeypatch):
    from minicode import openai_adapter as adapter, agent_loop
    from minicode import workflow_runner
    from minicode.deterministic_verifier import DeterministicVerifier

    class LocalFixtureSandbox:
        def wrap_command(self, *, argv, cwd, **kwargs):
            assert "minicode" in str(cwd)
            return argv

    # This test checks real public pytest results in disposable copies, not
    # OS sandbox availability inside the test runner's outer sandbox.
    monkeypatch.setattr(workflow_runner, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=LocalFixtureSandbox(),
            explicit_trusted_executables={"python3": sys.executable}))
    output = tmp_path / "run"
    output.mkdir()
    document = task_document()
    trigger.shared.write_json(output / "tasks.json", document)
    first = trigger.schedule_for()[0]
    manifest = {**trigger.static_contract(), "routeSha256": "a" * 64,
                "resolvedTransportRouteSha256": trigger.transport.route_fingerprint(RUNTIME),
                "productionSha256": "c" * 64, "harnessSha256": "d" * 64,
                "taskSha256": trigger.shared.sha((output / "tasks.json").read_bytes()),
                "publicVerificationFixturePolicy": trigger.PUBLIC_FIXTURE_POLICY,
                "publicVerificationFixtures": [], "publicPreflightSha256": "e" * 64}
    trigger.shared.write_json(output / "manifest.json", manifest)
    monkeypatch.setattr(trigger, "validate", lambda *_a, **_k: manifest)
    module = trigger.runner(output)
    monkeypatch.setitem(module.execute_trial.__globals__, "validate_public_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "frozen_runtime", lambda: {
        **RUNTIME, "subagentRoutingEnabled": False, "reflectionSynthesizerMode": "rule",
        "memoryHybridEnabled": False, "allowRemoteMemoryEmbedding": False,
        "intentSemanticClassifier": {"enabled": False}, "skillCandidateReranker": {"enabled": False},
        "agentTurnBudget": {"maxTokens": 500000, "maxModelCalls": 40, "maxCostUsd": "5"},
    })
    monkeypatch.setattr(module, "route_hash", lambda _: "a" * 64)
    monkeypatch.setattr(module, "production_sha", lambda: "c" * 64)
    monkeypatch.setattr(module, "harness_sha", lambda: "d" * 64)
    actions = [("read_file", {"path": "minicode/a.py"})] * 8 + [
        ("write_file", {"path": "tests/test_eval_regression.py", "content": "from minicode.a import VALUE\ndef test_changed(): assert VALUE == 2\n"}),
        ("edit_file", {"path": "minicode/a.py", "old": "VALUE = 1", "new": "VALUE = 2"}),
    ]
    sent = []

    def offline_send(request, **kwargs):
        body = json.loads(request.data)
        sent.append(body)
        if len(sent) > len(actions) + 1:
            # The complete current host performs a native completion review.
            # Keep its real refusal/continuation behavior and verify evidence
            # survives an unavailable review provider; do not bypass the gate.
            raise OSError("offline fixture: native completion review unavailable")
        if len(sent) <= len(actions):
            name, arguments = actions[len(sent) - 1]
            return ScriptedResponse({"tool_calls": [{"index": 0, "id": f"call-{len(sent)}",
                "function": {"name": name, "arguments": json.dumps(arguments)}}]})
        return ScriptedResponse({"content": "Updated VALUE and added a passing regression test."})

    monkeypatch.setattr(adapter, "open_verified_url", offline_send)
    real_loop = agent_loop.run_agent_turn
    policies = []

    def observe_loop(**kwargs):
        policies.append(kwargs.get("bounded_implementation_phases", False))
        return real_loop(**kwargs)

    monkeypatch.setattr(agent_loop, "run_agent_turn", observe_loop)
    workspace = None
    try:
        module.execute_trial(output, first["id"])
        root = output / "private" / first["id"]
        workspace = Path(trigger.shared.read_json(root / "workspace.json")["path"])
        assert not (root / "error.json").exists(), (root / "error.json").read_text() if (root / "error.json").exists() else ""
        assert policies and not any(policies)
        assert (workspace / "minicode/a.py").read_text() == "VALUE = 2\n"
        assert (workspace / "tests/test_eval_regression.py").is_file()
        assert len(actions) + 1 < len(sent) <= 40
        names = {item["function"]["name"] for item in sent[0]["tools"]}
        assert {"task", "read_file", "edit_file", "write_file"} <= names
        assert not any("PRIVATE_ORACLE_" in p.read_text(errors="ignore") for p in workspace.rglob("*") if p.is_file())
        records = [json.loads(p.read_text()) for p in sorted((root / "parent-requests").glob("*.json"), key=lambda p: int(p.stem))]
        assert [r["body"] for r in records] == sent
        snapshot = trigger.transport.SharedLedger(trigger.transport.TransportConfig(**trigger.shared.read_json(root / "transport-config.json"))).snapshot()
        limits = trigger.shared.read_json(root / "transport-config.json")
        execution = trigger.shared.read_json(root / "execution-start.json")
        assert limits["max_model_calls"] == 40 and limits["max_total_tokens"] == 500000
        assert limits["max_output_tokens"] == 8192
        assert limits["connect_timeout_seconds"] == 30
        assert limits["idle_timeout_seconds"] == 120 and limits["request_timeout_seconds"] == 300
        assert execution["deadlineEpoch"] - execution["epoch"] == 600
        assert snapshot["completed"] == snapshot["providerUsageCalls"] == len(actions) + 1
        assert snapshot["failed"] == len(sent) - len(actions) - 1
        assert snapshot["unknownUsageCalls"] == len(sent) - len(actions) - 1
        assert not snapshot["usageComplete"]
        assert [r["callId"] for r in records] == [r["call"] for r in snapshot["requests"]]
        from scripts.audit_multi_agent_trigger import archived_guidance, guidance_observation
        from io import BytesIO
        import zipfile
        archived = BytesIO()
        with zipfile.ZipFile(archived, "w") as bundle:
            for name in ("minicode/coding_progress.py", "minicode/prompt.py", "minicode/tools/task.py"):
                bundle.writestr(name, (trigger.ROOT / name).read_bytes())
        with zipfile.ZipFile(archived) as bundle:
            guidance = archived_guidance(bundle)
        presented = [guidance_observation(record["body"], call, guidance)
                     for record, call in zip(records, snapshot["requests"])]
        assert presented[0]["staticSystemGuidePresent"] and presented[0]["staticTaskGuidePresent"]
        assert presented[0]["taskAvailable"] and presented[0]["directWriteAvailable"]
        assert not any(p["dynamicHintPresent"] for p in presented[:8])
        assert presented[8]["dynamicHintPresent"]
        assert presented[8]["responseTools"] == ["write_file"]  # actual subsequent choice
        assert (root / "transcript.json").is_file()
    finally:
        if workspace is None:
            location = output / "private" / first["id"] / "workspace.json"
            if location.exists():
                workspace = Path(trigger.shared.read_json(location)["path"])
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
