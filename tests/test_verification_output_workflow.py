"""Real managed pytest output survives previews and same-Executor repair.

Model decisions are scripted. Dispatch, tools, process execution, candidate
isolation, verification, review, durable audit and commit are production paths.
"""

import base64
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tooling import ToolContext, ToolDefinition, ToolExecutionAbandoned, ToolRegistry, ToolResult
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


MARKER = "MIDDLE_DIAGNOSTIC_USE_REVISION_2"
INITIAL = "def value():\n    return 0\n"


def _source(value):
    return f"def value():\n    return {value}\n"


def _test(value):
    return ("import sys\nfrom candidate import value\n"
            "def test_value():\n"
            f"    if value() != {value}:\n"
            f"        sys.stderr.write('A' * 6000 + {MARKER!r} + 'Z' * 6000)\n"
            f"    assert value() == {value}\n")


def _call(name, value, ident):
    return {"id": ident, "toolName": name, "input": value}


def _result(messages, ident):
    content = next(m["content"] for m in reversed(messages)
                   if m.get("role") == "tool_result" and m.get("toolUseId") == ident)
    return json.JSONDecoder().raw_decode(content.lstrip())[0]


def _failed_manifest(messages):
    feedback = next(m for m in reversed(messages) if m.get("_implementation_batch_feedback"))
    document = json.loads(feedback["content"].split("\n", 1)[1])
    receipt = next(r for r in document["checks"] if r["checkId"] == "regression")
    assert receipt["status"] == "failed" and receipt["evidence"]["exitCode"] == 1
    assert MARKER not in receipt["evidence"]["stderr"]["preview"]
    assert receipt["evidence"]["stderr"]["byteCount"] > 12000
    # Baseline fails here after a real failed check, not at a new API import.
    assert receipt.get("verificationOutputs"), "real failed check lacks a readable full-output reference"
    return receipt["verificationOutputs"][0]


def _native_submit(tools):
    count = next(t for t in tools.list() if t.name == "executor_submit").input_schema[
        "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
    return {"status": "completed", "steps": [{"step": n, "status": "completed"}
            for n in range(1, count + 1)], "deviations": [], "blockers": []}


def _bootstrap(monkeypatch, verifier=None):
    class Sandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    verifier = verifier or DeterministicVerifier(sandbox_backend=Sandbox(),
                                                explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.subagent_types._read_max_turns_override", lambda _name: None)
    monkeypatch.delenv("MINICODE_TASK_TIMEOUT_SECONDS", raising=False)
    return verifier


def test_same_executor_reads_complete_stderr_across_review_repair_and_delivers(tmp_path, monkeypatch):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_compat.py").write_text("from candidate import value\ndef test_int():\n    assert isinstance(value(), int)\n")
    workers, adapters, checks = [], [], []
    original_worker = task_module._run

    def worker(data, context):
        entry = {"role": context._logical_agent_role, "session": data.get("_executor_session"),
                 "context": context, "cap": data.get("_executor_check_capability"),
                 "output_access": data.get("_verification_output_access")}
        workers.append(entry)
        result = original_worker(data, context)
        if entry["session"]:
            entry.update(model=entry["session"].model, history=entry["session"].context_manager)
        return result

    class Executor:
        count = 0
        tools = None
        output_ref = None

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            tools = tools_override or self.tools
            if self.count == 1:
                actions = [_call("edit_file", {"path": "candidate.py", "old": INITIAL, "new": _source(1)}, "first-edit"),
                           _call("executor_test", {"path": "test_regression.py", "code": _test(2)}, "first-test")]
            elif self.count == 2:
                manifest = _failed_manifest(messages)
                self.output_ref = manifest["streams"]["stderr"]["outputRef"]
                assert "verification_output_read" in tools.list_all()
                actions = [_call("verification_output_read", {"outputRef": self.output_ref, "offset": 6000, "limit": len(MARKER)}, "full-log")]
            elif self.count == 3:
                page = _result(messages, "full-log")
                assert base64.b64decode(page["contentBase64"]) == MARKER.encode()
                assert page["offset"] == 6000 and page["nextOffset"] == 6000 + len(MARKER)
                actions = [_call("edit_file", {"path": "candidate.py", "old": _source(1), "new": _source(2)}, "repair-from-log")]
            elif self.count in (4, 7):
                actions = [_call("executor_submit", _native_submit(tools), f"submit-{self.count}")]
            elif self.count == 5:
                # The same model and canonical context retain the original
                # failed-check reference across a fresh Assignment authority.
                assert any(m.get("toolUseId") == "full-log" for m in messages)
                actions = [_call("verification_output_read", {"outputRef": self.output_ref, "offset": 6000, "limit": len(MARKER)}, "old-log-after-review")]
            elif self.count == 6:
                assert base64.b64decode(_result(messages, "old-log-after-review")["contentBase64"]) == MARKER.encode()
                actions = [_call("edit_file", {"path": "candidate.py", "old": _source(2), "new": _source(3)}, "review-repair"),
                           _call("executor_test", {"path": "test_regression.py", "code": _test(3)}, "review-test-repair")]
            else:
                raise AssertionError("unexpected extra Executor call")
            return AgentStep(type="tool_calls", calls=actions)

    executor = Executor()

    class Reviewer:
        tools = None
        count = 0

        def __init__(self, revision):
            self.revision = revision

        def next(self, messages, tools_override=None, **_kwargs):
            self.count += 1
            assert target.read_text() == INITIAL
            if self.count == 1:
                actions = [_call("review_test", {"criterionIds": ["value_three"], "scenario": "The public result must be 3.",
                    "code": "from candidate import value\ndef test_independent_value():\n    assert value() == 3\n"}, "probe")]
            elif self.count == 2:
                receipt = _result(messages, "probe")
                assert receipt["status"] == ("failed" if self.revision == 1 else "passed")
                assert receipt.get("verificationOutputs")
                self.probe_id = receipt["probeId"]
                actions = [_call("verification_output_read", {"outputRef": receipt["verificationOutputs"][0]["streams"]["stdout"]["outputRef"]}, "review-log")]
            else:
                assert self.count == 3 and _result(messages, "review-log")["textExact"]
                good = self.revision == 2
                actions = [_call("review_submit", {"verdict": "approved" if good else "changes_required",
                    "criteria": [{"criterionId": "value_three", "status": "satisfied" if good else "failed",
                        "rationale": "The independent public-result check ran on this candidate.", "probeIds": [self.probe_id], "sourceEvidence": []}],
                    "blockingFindings": [] if good else [{"criterionIds": ["value_three"], "statement": "The independent check still gets 2 instead of the required 3; repair implementation and regression."}],
                    "warnings": [], "requestedChecks": [], "confidence": "high"}, "review-submit")]
            return AgentStep(type="tool_calls", calls=actions)

    def factory(_runtime, agent_type, **kwargs):
        model = executor if agent_type == "general" else Reviewer(1 + sum(kind != "general" for kind, _ in adapters))
        model.tools = kwargs["tools"]
        adapters.append((agent_type, model))
        return model

    class Sandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            result = super().run(**kwargs)
            checks.extend({"exitCode": c["exitCode"], "cwd": kwargs["cwd"],
                           "formal": getattr(kwargs.get("evidence_sink"), "__name__", "") == "per_check_sink"}
                          for c in result["checks"])
            return result

    _bootstrap(monkeypatch, Verifier(sandbox_backend=Sandbox(), explicit_trusted_executables={"python": sys.executable}))
    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([edit_file_tool, read_file_tool]))
    request = {"requestVersion": 1, "profile": {"taskId": "task_output_repair", "kind": "change", "risk": "low", "complex": False, "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_regression.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": [{"id": "value_three", "kind": "behavior", "text": "value() must return 3."}],
        "verificationChecks": [{"checkId": cid, "kind": "tests", "argv": ["python", "-m", "pytest", "-q", "-s", path], "criterionIds": ["value_three"]}
                               for cid, path in (("compat", "test_compat.py"), ("regression", "test_regression.py"))]}
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="output readback integration",
                     journal_factory=lambda root: RunJournal(root, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "repair value", "agent_type": "workflow",
            "prompt": "Make value() return 3 and add regression coverage. Independently review the change.", "workflow_request": request},
            ToolContext(cwd=str(tmp_path), _runtime={"model": "scripted-local"}, _agent_budget=AgentTurnBudget(),
                        _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert result.ok and trace and trace["committed"], result.output
    assert target.read_text() == _source(3)
    assert (tmp_path / "test_regression.py").read_text() == _test(3)
    executors = [w for w in workers if w["role"] == "executor"]
    assert len(executors) == 2 and len({id(w["session"]) for w in executors}) == 1
    assert len({id(w["model"]) for w in executors}) == len({id(w["history"]) for w in executors}) == 1
    assert any(c["exitCode"] == 1 for c in checks)
    assert [c["exitCode"] for c in checks if c["formal"]] == [0, 0, 0, 0]
    assert all(not Path(c["cwd"]).exists() for c in checks)
    journal = RunJournal(tmp_path, data_dir=data_dir)
    assert journal.get_scheduler_audit(run_id, trace["workflowId"]) is not None
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    sessions = [e.payload for child in journal.list_subagent_runs(run_id)
                for e in journal.list_subagent_events(run_id, child.subagent_id) if e.type == "execution.executor_session"]
    assert len(sessions) == 2 and len({s["sessionId"] for s in sessions}) == 1
    assert sum(s["modelReused"] and s["resumed"] and s["priorMessageCount"] > 0 for s in sessions) == 1
    assert list(data_dir.rglob("*_stderr.bin")), "complete bytes persist after clones and owner capabilities close"
    for entry in workers:
        access = entry["cap"]._output_reader if entry["cap"] else entry["output_access"]
        with pytest.raises(ValueError, match="closed"):
            access.tool.run({"outputRef": executor.output_ref}, entry["context"])


def test_primary_has_the_same_full_output_readback_and_revocation(tmp_path, monkeypatch):
    _bootstrap(monkeypatch)
    (tmp_path / "candidate.py").write_text(_source(1))
    (tmp_path / "test_regression.py").write_text(_test(2))
    spec = ImplementationSpec(("candidate.py",), (VerificationCheck("regression", "tests",
        ("python", "-m", "pytest", "-q", "-s", "test_regression.py"), 20),), "Repair value")
    capability = direct_verification_capability(spec)
    context = ToolContext(cwd=str(tmp_path), _agent_budget=AgentTurnBudget())
    registry = capability.bind_registry(ToolRegistry([edit_file_tool]))
    try:
        actual = registry.execute("executor_check", {"check": 1}, context)
        receipt = json.loads(actual.output)
        assert receipt.get("verificationOutputs"), "primary check lacks complete-output references"
        ref = receipt["verificationOutputs"][0]["streams"]["stderr"]["outputRef"]
        page = registry.execute("verification_output_read", {"outputRef": ref, "offset": 6000, "limit": len(MARKER)}, context)
        assert page.ok and base64.b64decode(json.loads(page.output)["contentBase64"]) == MARKER.encode()
        assert registry.execute("verification_output_read", {"outputRef": ref}, replace(context, _step=2)).ok
        assert not registry.execute("verification_output_read", {"outputRef": ref}, replace(context, _agent_budget=AgentTurnBudget())).ok
        assert not registry.execute("verification_output_read", {"outputRef": ref}, replace(context, _agent_depth=1)).ok
        assert not registry.execute("verification_output_read", {"outputRef": "verification_output_" + "0" * 32 + "_stderr"}, context).ok
        assert not registry.execute("verification_output_read", {"outputRef": ref}, replace(context, cwd=str(tmp_path.parent))).ok
        tool = next(t for t in registry.list() if t.name == "verification_output_read")
        with pytest.raises(TaskDeadlineExceeded):
            tool.run({"outputRef": ref}, replace(context, _task_deadline_monotonic=time.monotonic() - 1))
        abandoned = threading.Event()
        abandoned.set()
        with pytest.raises(ToolExecutionAbandoned):
            tool.run({"outputRef": ref}, replace(context, _tool_abandoned=abandoned))
        token = TurnCancellationToken("turn_" + "1" * 32)
        token.request()
        with pytest.raises(TurnCancellationRequested):
            tool.run({"outputRef": ref}, replace(context, _cancellation_token=token))
    finally:
        capability.close()
    assert not registry.execute("verification_output_read", {"outputRef": ref}, context).ok


def test_actual_registry_pages_worst_case_escaped_output_without_truncating_json(tmp_path, monkeypatch):
    _bootstrap(monkeypatch)
    payload = b"\x00" * 17000 + "多字节".encode() + b"\xff"
    (tmp_path / "candidate.py").write_text(_source(1))
    (tmp_path / "test_log.py").write_text(
        "import sys\ndef test_log():\n"
        f"    sys.stderr.buffer.write({payload!r})\n"
        "    assert True\n")
    spec = ImplementationSpec(("candidate.py",), (VerificationCheck("log", "tests",
        ("python", "-m", "pytest", "-q", "-s", "test_log.py"), 20),), "Read diagnostic bytes")
    cap, foreign = direct_verification_capability(spec), direct_verification_capability(spec)
    context = ToolContext(cwd=str(tmp_path))
    registry = cap.bind_registry(ToolRegistry([edit_file_tool]))
    foreign_registry = foreign.bind_registry(ToolRegistry([edit_file_tool]))
    try:
        receipt = json.loads(registry.execute("executor_check", {"check": 1}, context).output)
        assert receipt.get("verificationOutputs"), "full output capture must be connected to primary checks"
        ref = receipt["verificationOutputs"][0]["streams"]["stderr"]["outputRef"]
        other = json.loads(foreign_registry.execute("executor_check", {"check": 1}, context).output)
        foreign_ref = other["verificationOutputs"][0]["streams"]["stderr"]["outputRef"]
        assert not registry.execute("verification_output_read", {"outputRef": foreign_ref}, context).ok
        offset, chunks = 0, []
        while True:
            result = registry.execute("verification_output_read", {"outputRef": ref, "offset": offset, "limit": 8192}, context)
            assert result.ok and len(result.output) < 30000
            page = json.loads(result.output)
            part = base64.b64decode(page["contentBase64"])
            assert page["nextOffset"] == offset + len(part)
            if offset == 0:
                assert page["nextOffset"] < 8192, "serialized JSON size must shrink the worst-case page"
            chunks.append(part)
            offset = page["nextOffset"]
            if page["eof"]:
                break
        assert b"".join(chunks) == payload
    finally:
        cap.close()
        foreign.close()


def test_bounded_request_views_expose_log_reader_only_before_native_handoff():
    from minicode.implementation_phases import ImplementationPhase, ImplementationPhaseController

    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    before = budget.snapshot()
    cap = direct_verification_capability(ImplementationSpec(("app.py",), (), "inspect diagnostics"))
    registry = cap.bind_registry(ToolRegistry([edit_file_tool, read_file_tool]))
    policy = ImplementationPhaseController(snapshot=before, max_steps=30, now=0,
        deadline=600, bounded=True, analysis_complete=True)
    try:
        policy.request(budget.snapshot(), estimated_input=5000, now=0)
        assert policy.phase is ImplementationPhase.IMPLEMENT
        assert "verification_output_read" in {t.name for t in policy.tool_view(registry).list()}
        policy.phase = ImplementationPhase.FINALIZE
        assert "verification_output_read" in {t.name for t in policy.tool_view(registry).list()}
        # Only the surface identity is needed here; this placeholder is never
        # executed and does not mint a result channel or approval authority.
        native_registry = registry.overlay([ToolDefinition(name="executor_submit", description="native surface",
            input_schema={"type": "object"}, validator=lambda value: value,
            run=lambda _value, _context: ToolResult(ok=False, output="not executed"))])
        policy.handoff_only = True
        assert {t.name for t in policy.tool_view(native_registry).list()} == {"executor_submit"}
        policy.handoff_only = False
        policy.conditional_handoff = True
        assert "verification_output_read" not in {t.name for t in policy.tool_view(native_registry).list()}
        policy.conditional_handoff = False
        assert "verification_output_read" not in {t.name for t in policy.tool_view(native_registry).list()}
        assert budget.snapshot() == before
    finally:
        cap.close()
