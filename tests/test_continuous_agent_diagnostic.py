"""Offline diagnostics plumbing; no live provider or historical judge is run."""

import json
import subprocess
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_transport import (
    DONE,
    MESSAGES,
    RUNTIME,
    TEXT,
    USAGE,
    ScriptedResponse,
    config,
    event,
)
from test_implementation_progress_live import fixture_source

from minicode import openai_adapter as adapter
from minicode.agent_budget import AgentTurnBudget
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.tooling import ToolRegistry
from scripts import run_continuous_agent_diagnostic as diagnostic
from scripts import run_three_arm_real as shared
from scripts.evaluation_transport import (
    EvaluationTransport,
    SharedLedger,
    TransportConfig,
)


def test_unlimited_transport_keeps_complete_usage_and_request_output_cap(tmp_path, monkeypatch):
    captured = []

    def response(request, *, timeout):
        captured.append((json.loads(request.data), timeout,
                         request._minicode_connect_timeout_seconds))
        usage = {**USAGE, "prompt_tokens": 700000, "total_tokens": 700008}
        return ScriptedResponse([event({**TEXT, "usage": usage}), DONE])

    monkeypatch.setattr(adapter, "open_verified_url", response)
    cfg = replace(config(tmp_path), deadline_epoch=None, max_model_calls=None,
                  max_total_tokens=None, max_output_tokens=8192, request_timeout_seconds=300)
    assert TransportConfig(**json.loads(json.dumps(asdict(cfg)))) == cfg
    with EvaluationTransport(cfg) as transport:
        assert transport.deadline is None
        model = adapter.OpenAIModelAdapter(dict(RUNTIME), None)
        for _ in range(2):
            assert model.next(MESSAGES, max_output_tokens_override=9000).content == "hello"
        snapshot = transport.ledger.snapshot()
    assert snapshot["started"] == snapshot["completed"] == 2
    assert snapshot["chargedTokens"] == 1400016 and snapshot["usageComplete"]
    assert snapshot["admissionRejectedCount"] == 0
    assert len(captured) == 2
    assert all(body["max_tokens"] == 8192 for body, _timeout, _connect in captured)
    # urllib's timeout also covers response headers; the physical connect/TLS
    # bound is separate host metadata and must retain its smaller limit.
    assert all(timeout == cfg.idle_timeout_seconds == 60
               and connect == cfg.connect_timeout_seconds == 30
               for _body, timeout, connect in captured)
    assert all(299 < row["requestDeadlineEpoch"] - row["startedEpoch"] <= 300
               for row in snapshot["requests"])
    assert SharedLedger(cfg).snapshot()["chargedTokens"] == 1400016


def test_no_cumulative_deadline_does_not_disable_per_request_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "open_verified_url", lambda *_a, **_k:
                        ScriptedResponse([b": heartbeat\n\n"] * 100, delay=0.015))
    cfg = replace(config(tmp_path), deadline_epoch=None, max_model_calls=None,
                  max_total_tokens=None, request_timeout_seconds=0.06, idle_timeout_seconds=0.2)
    with EvaluationTransport(cfg) as transport:
        with pytest.raises(ModelCallDeadlineExceeded):
            adapter.OpenAIModelAdapter(dict(RUNTIME), None).next(MESSAGES)
        snapshot = transport.ledger.snapshot()
    assert snapshot["started"] == snapshot["failed"] == snapshot["unknownUsageCalls"] == 1
    assert snapshot["requests"][0]["reservedTokens"] > 0


@pytest.mark.parametrize("field", ["max_output_tokens",
                                  "idle_timeout_seconds", "connect_timeout_seconds"])
def test_provider_limits_cannot_be_disabled_with_null(tmp_path, field):
    with pytest.raises(ValueError):
        replace(config(tmp_path), **{field: None})


def test_guardian_stops_setup_but_does_not_reapply_it_after_null_execution_deadline(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(shared, "time", SimpleNamespace(time=lambda: clock.value))
    killed = []
    monkeypatch.setattr(shared, "stop_trial", lambda *a: killed.append(a) or {"cleanupComplete": True})

    class Process:
        pid = 12345
        returncode = None
        waits = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.waits += 1
            if timeout == 10:
                self.returncode = -15
                return self.returncode
            if self.waits < 3:
                clock.value += 100
                raise subprocess.TimeoutExpired("scripted", timeout)
            self.returncode = 0
            return 0

    shared.write_json(tmp_path / "execution-start.json", {"epoch": 100, "deadlineEpoch": None})
    assert shared.wait_trial_process(Process(), tmp_path) == 0
    assert clock.value == 300 and killed == []
    (tmp_path / "execution-start.json").unlink()
    process = Process()
    assert shared.wait_trial_process(process, tmp_path, setup_seconds=30) < 0
    assert len(killed) == 1


def test_private_runner_preserves_original_task_but_removes_old_budget_instructions(tmp_path):
    original = dict(shared.LIMITS)
    original_prompt = diagnostic.progress.prompt_for_arm
    module = diagnostic.runner(tmp_path)
    assert Path(module.__file__).name == "run_continuous_agent_diagnostic.py"
    assert module.LIMITS == diagnostic.LIMITS and shared.LIMITS == original
    assert diagnostic.progress.prompt_for_arm is original_prompt
    prompt = "UNCHANGED_PUBLIC_TASK: fix behavior; preserve the documented interface."
    rendered = module.prompt_for_arm(prompt, "explicit")
    assert rendered.startswith(prompt) and rendered.count(prompt) == 1
    assert "tests/test_eval_regression.py" in rendered
    assert "1800秒执行预算" not in rendered and "有界修复" not in rendered
    assert "500000" not in rendered and "40次" not in rendered
    assert "不设置累计" in rendered and "131072" in rendered
    with pytest.raises(ValueError, match="only"):
        module.prompt_for_arm(prompt, "single")


def _freeze_offline(tmp_path, monkeypatch):
    """Preflight receipt is supplied only for manifest-binding tests."""
    source, output = tmp_path / "source", tmp_path / "diagnostic"
    original = fixture_source(source)
    diagnostic.prepare(output, source)
    assert diagnostic.shared.read_json(output / "tasks.json")["cases"] == original["cases"][:3]
    from test_public_preflight_reports import reporting_preflight_fixture
    monkeypatch.setattr(diagnostic.progress, "run_public_preflight", reporting_preflight_fixture)
    real_runner = diagnostic.runner

    def offline_runner(path):
        module = real_runner(path)
        module.frozen_runtime = lambda: {"model": "glm-5.3-flash",
            "maxOutputTokens": 131072, "modelTimeoutSeconds": None,
            "agentTurnBudget": {"maxTokens": None, "maxModelCalls": None, "maxCostUsd": None}}
        module.route_hash = lambda _: "a" * 64
        module.resolved_transport_route = lambda _: "b" * 64
        return module

    monkeypatch.setattr(diagnostic, "runner", offline_runner)
    diagnostic.freeze(output)
    return output


def test_diagnostic_freezes_separate_manifest_archive_and_original_task_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS", "3")
    output = _freeze_offline(tmp_path, monkeypatch)
    manifest = diagnostic.validate(output)
    assert manifest["phase"] == "diagnostic" and manifest["sameBudgetComparison"] is False
    assert manifest["schedule"] == diagnostic.SCHEDULE
    assert all(manifest["limits"][key] is None for key in
               ("maxTokens", "maxModelCalls", "maxCostUsd", "wallSeconds"))
    assert manifest["roleTurnLimits"] == {"explore": 0, "plan": 0, "general": 0}
    assert manifest["parentMaxSteps"] is None
    with zipfile.ZipFile(output / "frozen-code.zip") as bundle:
        for name in diagnostic.HARNESS_FILES:
            assert bundle.read("scripts/" + name) == (diagnostic.ROOT / "scripts" / name).read_bytes()
    import os
    assert os.environ["MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS"] == "3"
    with pytest.raises(ValueError, match="never overwrite"):
        diagnostic.freeze(output)
    manifest["limits"]["maxTokens"] = 500000
    diagnostic.shared.write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="limits"):
        diagnostic.validate(output)


def test_actual_shared_child_setup_receives_null_owner_and_budget(tmp_path, monkeypatch):
    import importlib

    from minicode import agent_runtime

    north = importlib.import_module("run_north_star_live")

    output = _freeze_offline(tmp_path, monkeypatch)
    received = []

    def factory(**kwargs):
        import os
        received.append(kwargs)
        assert kwargs["deadline_monotonic"] is None
        assert "independent" not in repr(kwargs["implementation_spec"])
        assert all(os.environ[name] == "0" for name in (
            "MINI_CODE_TURN_BUDGET_TOKENS", "MINI_CODE_TURN_BUDGET_MODEL_CALLS",
            "MINI_CODE_TURN_BUDGET_COST_USD", "MINICODE_TASK_TIMEOUT_SECONDS",
            "MINI_CODE_SUBAGENT_GENERAL_MAX_TURNS"))
        return SimpleNamespace(model=object(), permissions=SimpleNamespace(),
            tools=ToolRegistry([]), runtime={}, system_prompt="public system prompt",
            agent_budget=AgentTurnBudget(max_total_tokens=None, max_model_calls=None, max_cost_usd=None),
            execute=lambda messages, _observation: [*messages, {"role": "assistant", "content": "offline boundary only"}],
            dispose=lambda: None)

    monkeypatch.setattr(agent_runtime, "create_agent_turn_runtime", factory)
    monkeypatch.setattr(north, "_runtime_profile_from_adapter", lambda *_: {})
    monkeypatch.setattr(north, "_runtime_profile_from_runtime", lambda *_: {})
    diagnostic.runner(output).execute_trial(output, diagnostic.SCHEDULE[0]["id"])
    assert len(received) == 1
    root = output / "private" / diagnostic.SCHEDULE[0]["id"]
    assert diagnostic.shared.read_json(root / "execution-start.json")["deadlineEpoch"] is None
    cfg = diagnostic.shared.read_json(root / "transport-config.json")
    assert cfg["deadline_epoch"] is cfg["max_model_calls"] is cfg["max_total_tokens"] is None
    assert cfg["request_timeout_seconds"] is None and cfg["max_output_tokens"] == 131072
    execution = diagnostic.shared.read_json(root / "execution.json")
    assert execution["errorClass"] is None
    assert execution["budget"]["used_model_calls"] == 0


def test_manual_interrupt_still_cleans_owned_processes(tmp_path, monkeypatch):
    module = diagnostic.runner(tmp_path)
    waits, cleaned = [], []
    proc = SimpleNamespace(pid=123, poll=lambda: None,
                           wait=lambda **kwargs: waits.append(kwargs) or 0)
    # The underlying guardian function's globals remain this private runner.
    monkeypatch.setattr(module, "read_json", lambda _path: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(module, "stop_trial", lambda *args: cleaned.append(args) or {"cleanupComplete": True})
    (tmp_path / "execution-start.json").write_text("{}")
    with pytest.raises(KeyboardInterrupt):
        module.wait_trial_process(proc, tmp_path)
    assert cleaned == [(123, tmp_path / "processes.jsonl")]
    assert waits == [{"timeout": 10}]
    assert diagnostic.shared.read_json(tmp_path / "process-cleanup.json")["cleanupComplete"]


def test_archive_verification_is_offline_and_historical_execution_is_rejected(tmp_path, monkeypatch):
    output = _freeze_offline(tmp_path, monkeypatch)
    monkeypatch.setattr(diagnostic, "runner", lambda *_: pytest.fail("audit must not load runtime"))
    monkeypatch.setattr(diagnostic, "_prepared_document", lambda *_: pytest.fail("audit must not parse tasks"))
    receipt = diagnostic.verify_frozen(output)
    assert receipt["valid"] and receipt["auditOnly"]
    assert {"productionSha256", "harnessSha256", "frozenCodeArchiveSha256"}.issubset(receipt["verifiedHashes"])
    # The old policy header is allowed for independent byte-integrity checking,
    # never for execution by this current runner. No archived code is imported.
    manifest = diagnostic.shared.read_json(output / "manifest.json")
    manifest["protocolVersion"] = "continuous-retained-task-diagnostic-v1"
    manifest["limits"].update(maxOutputTokens=8192, requestTimeoutSeconds=300)
    diagnostic.shared.write_json(output / "manifest.json", manifest)
    assert diagnostic.verify_frozen(output)["valid"]
    with pytest.raises(ValueError, match="audit-only"):
        diagnostic.validate(output)
    (output / "tasks.json").write_text("tampered bytes")
    with pytest.raises(ValueError, match="taskSha256"):
        diagnostic.verify_frozen(output)
