"""One response's contiguous writes earn feedback on their final real bytes."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep, ModelUsage


class Evidence:
    def __init__(self):
        self.records = []

    def emit(self, *args, **kwargs):
        pass

    def record_tool_evidence(self, **record):
        self.records.append(record)


@pytest.mark.parametrize("last_edit_fails", [False, True])
@pytest.mark.parametrize("prebound", [False, True], ids=["primary", "delegated-capability"])
def test_six_write_response_runs_two_real_checks_on_final_candidate(tmp_path, monkeypatch, last_edit_fails, prebound):
    import minicode.workflow_runner as runner
    from minicode.implementation_execution import direct_verification_capability

    (tmp_path / "candidate.py").write_text("value = 0\n")
    (tmp_path / "test_public.py").write_text(
        "from candidate import value\ndef test_value(): assert 0 <= value <= 6\n")
    checks, observed_messages = [], []
    evidence = Evidence()

    def command(argv, **kwargs):
        current = Path(kwargs["cwd"], "candidate.py").read_text()
        completed = subprocess.run([sys.executable, "-m", *argv], **kwargs)
        checks.append((current, completed.returncode, Path(kwargs["cwd"])))
        return completed

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context", lambda *_a, **_k:
                        DeterministicVerifier(command_runner=command))

    class Model:
        calls = 0

        def next(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": f"edit-{i}", "toolName": "edit_file", "input": {
                        "path": "candidate.py", "old": "missing anchor" if i == 6 and last_edit_fails else f"value = {i-1}",
                        "new": f"value = {i}",
                    },
                } for i in range(1, 7)], usage=ModelUsage(input_tokens=100, output_tokens=100, source="provider"))
            observed_messages.extend(messages)
            return AgentStep(type="assistant", kind="final", content="The requested regression file remains missing.",
                             usage=ModelUsage(input_tokens=100, output_tokens=100, source="provider"))

    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    model = Model()
    spec = ImplementationSpec(("candidate.py", "test_public.py", "test_regression.py"), (
        VerificationCheck("public", "tests", ("pytest", "-q", "test_public.py"), 10),
        VerificationCheck("regression", "tests", ("pytest", "-q", "test_regression.py"), 10),
    ), "Implement the candidate and public regression.")
    registry = ToolRegistry([edit_file_tool, read_file_tool])
    cap = direct_verification_capability(spec) if prebound else None
    # This fixture measures one write batch and its next feedback observation.
    # The primary now rejects failed-check finals and keeps implementing; an
    # explicit caller boundary isolates batching without accepting that final.
    step_limit = 20 if prebound else 2
    messages = run_agent_turn(model=model, tools=cap.bind_registry(registry) if cap else registry,
        messages=[{"role": "user", "content": "Implement the six related candidate changes and verify."}],
        cwd=str(tmp_path), implementation_task=True, implementation_analysis_complete=True,
        implementation_spec=spec,
        event_sink=evidence, agent_budget=budget, enable_work_chain=False, max_steps=step_limit)
    final_value = 5 if last_edit_fails else 6
    assert len(checks) == 2, "Each intermediate edit reran the whole check suite."
    assert [(source, code) for source, code, _ in checks] == [(f"value = {final_value}\n", 0), (f"value = {final_value}\n", 4)]
    assert all(path != tmp_path and not path.exists() for _, _, path in checks)
    original = [m for m in observed_messages if m.get("role") == "tool_result" and str(m.get("toolUseId", "")).startswith("edit-")]
    assert len(original) == 6
    assert sum(bool(m.get("isError")) for m in original) == int(last_edit_fails)
    assert all("IMPLEMENTATION CHECK FEEDBACK" not in m["content"] for m in original)
    feedback = [m for m in observed_messages if m.get("_implementation_batch_feedback")]
    assert len(feedback) == 1, "The next model request must receive the real batch feedback."
    receipt = json.loads(feedback[0]["content"].split("\n", 1)[1])
    assert receipt["status"] == "failed"
    assert receipt["changedCallIds"] == [f"edit-{i}" for i in range(1, final_value + 1)]
    assert [check["status"] for check in receipt["checks"]] == ["passed", "failed"]
    assert len({check["patchDigest"] for check in receipt["checks"]}) == 1
    assert receipt["patchDigest"] == receipt["checks"][0]["patchDigest"]
    persisted = [r for r in evidence.records if r.get("tool_name") == "implementation_batch_check"]
    assert len(persisted) == 1 and json.loads(persisted[0]["output"]) == receipt
    assert budget.snapshot().used_model_calls == model.calls == 2
    assert budget.snapshot().limit_total_tokens == 500000
    if cap:
        assert not cap._closed, "The delegated capability remains owned by its workflow."
        cap.close()
    else:
        assert any(m.get("_host_implementation_receipt") for m in messages), "A failed regression check must keep delivery incomplete."


def _edit(index):
    return {"id": f"edit-{index}", "toolName": "edit_file", "input": {
        "path": "candidate.py", "old": f"value = {index - 1}", "new": f"value = {index}"}}


@pytest.mark.parametrize("barrier", ["read_file", "executor_check", "executor_submit"])
def test_nonwrite_barriers_and_read_first_scheduler_preserve_observed_candidate(tmp_path, barrier):
    import hashlib
    from minicode.executor_verification import ExecutorCheckCapability
    from test_implementation_finalize_deadline import _submission_tools

    (tmp_path / "candidate.py").write_text("value = 0\n")
    _, _, _, channel, submit = _submission_tools(tmp_path)
    observed = []
    evidence = Evidence()

    def check(check, context):
        source = Path(context.cwd, "candidate.py").read_bytes()
        observed.append(source.decode())
        return {"checkId": check.check_id, "status": "passed", "patchDigest": "sha256:" + hashlib.sha256(source).hexdigest()}

    cap = ExecutorCheckCapability((VerificationCheck("public", "tests", ("pytest", "-q"), 10),), check)
    registry = cap.bind_registry(ToolRegistry([edit_file_tool, read_file_tool, submit]))
    barrier_input = ({"path": "candidate.py"} if barrier == "read_file" else {"check": 1}
        if barrier == "executor_check" else {"status": "completed", "steps": [{"step": 1, "status": "completed"}],
                                              "deviations": [], "blockers": []})

    class Model:
        calls = 0

        def next(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[_edit(1), _edit(2), {
                    "id": "barrier", "toolName": barrier, "input": barrier_input}, _edit(3), _edit(4)])
            assert any(m.get("_implementation_batch_feedback") for m in messages)
            return AgentStep(type="assistant", kind="final", content="Observed the approved checks.")

    model = Model()
    messages = run_agent_turn(model=model, tools=registry, messages=[{"role": "user", "content": "Make edits."}],
        cwd=str(tmp_path), implementation_task=True, enable_implementation_phases=False,
        enable_work_chain=False, event_sink=evidence, subagent_mailbox=channel, max_steps=10)
    assert observed == (["value = 2\n"] if barrier == "executor_submit" else
                        ["value = 2\n", "value = 2\n", "value = 4\n"] if barrier == "executor_check" else
                        ["value = 2\n", "value = 4\n"])
    feedback = [m for m in messages if m.get("_implementation_batch_feedback")]
    assert len(feedback) == (1 if barrier == "executor_submit" else 2)
    barrier_result = next(m for m in messages if m.get("role") == "tool_result" and m.get("toolUseId") == "barrier")
    assert not barrier_result["isError"]
    if barrier == "read_file":
        assert "value = 0" in barrier_result["content"], "Existing concurrent reads still execute before serial writes."
    if barrier == "executor_submit":
        assert model.calls == 1 and (tmp_path / "candidate.py").read_text() == "value = 2\n"
        records = [r["tool_name"] for r in evidence.records if r.get("phase") == "finished"]
        assert records.index("implementation_batch_check") < records.index("executor_submit")
        assert '"verification":"pending"' in barrier_result["content"], "Native declaration is not promotion."
    cap.close()


@pytest.mark.parametrize("interruption", ["writer_exception", "cancel", "deadline", "revoke"])
def test_partial_batch_interruption_keeps_error_and_persists_unverified_candidate(tmp_path, monkeypatch, interruption):
    from dataclasses import replace
    from minicode.executor_verification import ExecutorCheckCapability
    from minicode.turn_cancellation import TurnCancellationToken, TurnCancellationRequested

    (tmp_path / "candidate.py").write_text("value = 0\n")
    observed = []
    evidence = Evidence()
    token = TurnCancellationToken("turn_" + "a" * 32)

    def check(check, context):
        observed.append(Path(context.cwd, "candidate.py").read_text())
        return {"checkId": check.check_id, "status": "failed", "patchDigest": "sha256:" + "b" * 64,
                "evidence": {"stderr": {"preview": "actual check failure"}}}

    cap = ExecutorCheckCapability((VerificationCheck("public", "tests", ("pytest", "-q"), 10),), check)

    def write(value, context):
        result = edit_file_tool.run(value, context)
        if Path(context.cwd, "candidate.py").read_text() == "value = 2\n":
            if interruption == "writer_exception":
                raise RuntimeError("writer failed after changing bytes")
            if interruption == "cancel":
                token.request()
            if interruption == "deadline":
                context._task_deadline_monotonic = 0
            if interruption == "revoke":
                cap.close()
        return result

    registry = cap.bind_registry(ToolRegistry([replace(edit_file_tool, run=write)]))

    class Model:
        calls = 0

        def next(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[_edit(1), _edit(2)])
            assert any(m.get("_implementation_batch_feedback") for m in messages)
            return AgentStep(type="assistant", kind="final", content="Candidate remains incomplete.")

    model = Model()
    kwargs = dict(model=model, tools=registry, messages=[{"role": "user", "content": "Make edits."}],
        cwd=str(tmp_path), implementation_task=True, enable_implementation_phases=False,
        enable_work_chain=False, event_sink=evidence, cancellation_token=token, max_steps=10)
    if interruption == "cancel":
        with pytest.raises(TurnCancellationRequested):
            run_agent_turn(**kwargs)
        assert model.calls == 1
    else:
        messages = run_agent_turn(**kwargs)
        edits = [m for m in messages if m.get("role") == "tool_result" and m.get("toolName") == "edit_file"]
        assert len(edits) == 2
        assert edits[-1]["isError"] is (interruption == "writer_exception")
        if interruption == "writer_exception":
            assert "error[tool_crashed]" in edits[-1]["content"]
    assert (tmp_path / "candidate.py").read_text() == "value = 2\n"
    documents = [json.loads(r["output"]) for r in evidence.records if r.get("tool_name") == "implementation_batch_check"]
    assert len(documents) == 1
    receipt = documents[0]
    assert receipt["status"] == ("failed" if interruption == "writer_exception" else "inconclusive")
    assert observed == (["value = 2\n"] if interruption == "writer_exception" else [])
    if interruption == "writer_exception":
        assert receipt["uncertainWriteCallIds"] == ["edit-2"]
        assert "actual check failure" in json.dumps(receipt)
    else:
        assert receipt["patchDigest"] is None, "An unrun check cannot reuse stale candidate evidence."
        assert receipt["checks"][0]["reason"] == "batch_check_interrupted"
    # Cancellation can exit before transcript insertion; private write outputs
    # and the independent inconclusive batch receipt still survive that exit.
    original = [r for r in evidence.records if r.get("tool_name") == "edit_file" and r.get("phase") == "finished"]
    assert len(original) == 2
    cap.close()
