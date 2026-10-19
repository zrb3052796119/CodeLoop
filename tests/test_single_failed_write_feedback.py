"""A rejected single edit retains real feedback on the unchanged candidate.

The adapter is scripted; edit_file, isolated pytest, receipt capture, native
submission and shared-budget admission use production code. This proves
evidence retention, not a live model's ability to repair the candidate.
"""

from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import (
    DeterministicVerifier, VerificationCheck, VerificationDeadlineExceeded,
)
from minicode.executor_verification import ExecutorCheckCapability
from minicode.implementation_execution import (
    ImplementationSpec, direct_verification_capability, implementation_candidate_digest,
)
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolExecutionAbandoned, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep, ModelUsage
from test_executor_result_submission import _bound, _submission


FAILED_EDIT = {"path": "candidate.py", "old": "absent source anchor", "new": "value = 3"}
FEEDBACK = "\n\n=== IMPLEMENTATION CHECK FEEDBACK ===\n"


class Evidence:
    def __init__(self):
        self.records = []

    def emit(self, *_args, **_kwargs):
        pass

    def record_tool_evidence(self, **record):
        self.records.append(record)


def _case(tmp_path, monkeypatch, *, regression_passes=True):
    target = tmp_path / "candidate.py"
    target.write_text("value = 2\n")
    (tmp_path / "test_public.py").write_text(
        "from candidate import value\ndef test_public(): assert value >= 1\n")
    (tmp_path / "test_regression.py").write_text(
        f"from candidate import value\ndef test_regression(): assert value == {2 if regression_passes else 3}\n")
    spec = ImplementationSpec(("candidate.py", "test_public.py", "test_regression.py"), tuple(
        VerificationCheck(name, "tests", ("python", "-m", "pytest", "-q", f"test_{name}.py"), 10)
        for name in ("public", "regression")), "Verify the existing candidate.")
    observed = []

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            result = super().run(**kwargs)
            observed.append((Path(kwargs["cwd"]), result["checks"][0]["exitCode"]))
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)

    def digest(context):
        # Candidate identity must use the same file-type/framed-byte domain as
        # the real direct check callback; do not reconstruct an obsolete hash.
        return implementation_candidate_digest(spec, context)

    direct = direct_verification_capability(spec)
    cap = ExecutorCheckCapability(spec.checks, direct._run_check, current_patch_digest=digest)
    _, _, store, channel, submit = _bound()
    registry = cap.bind_registry(ToolRegistry([edit_file_tool, submit]))
    budget = AgentTurnBudget(max_model_calls=40, max_total_tokens=500000)
    evidence = Evidence()
    context = ToolContext(cwd=str(tmp_path), _subagent_mailbox=channel,
                          _task_deadline_monotonic=time.monotonic() + 1800,
                          _agent_budget=budget, _event_sink=evidence)
    return cap, registry, context, store, channel, target, observed, evidence, budget


@pytest.mark.parametrize("regression_passes", [False, True], ids=["failed-check", "all-checks-pass"])
def test_failed_single_edit_recaptures_current_receipts_without_acknowledging_error(
    tmp_path, monkeypatch, regression_passes,
):
    cap, registry, context, store, channel, target, observed, evidence, budget = _case(
        tmp_path, monkeypatch, regression_passes=regression_passes)
    for number in (1, 2):
        registry.execute("executor_check", {"check": number}, context)
    original = ToolRegistry([edit_file_tool]).execute("edit_file", FAILED_EDIT, context)
    before = target.read_bytes()
    budget_before = budget.snapshot()
    result = registry.execute("edit_file", FAILED_EDIT, context)
    assert not result.ok and not result._file_changed and result._edit_anchor_failed
    assert result.output.split(FEEDBACK)[0] == original.output
    assert len(observed) == 4, "A failed single write discarded receipts without rechecking current bytes"
    assert [code for _, code in observed] == [0, 0 if regression_passes else 1] * 2
    assert len({cwd for cwd, _ in observed}) == 4
    assert all(cwd != tmp_path and not cwd.exists() for cwd, _ in observed)
    assert target.read_bytes() == before == b"value = 2\n"
    assert result._implementation_checks_failed is (not regression_passes)
    # The ordinary loop observation must not erase freshly recovered receipts.
    cap.observe_tool_result("edit_file", FAILED_EDIT, result, context=context)
    snapshot = cap.capture_checked_candidate(context=context)
    assert snapshot is not None
    assert snapshot.handoff_reason == "executor_write_feedback_pending"
    receipts = [json.loads(value) for value in snapshot.receipts_json]
    assert [item["status"] for item in receipts] == ["passed", "passed" if regression_passes else "failed"]
    assert all(item["patchDigest"] == snapshot.patch_digest for item in receipts)
    assert [item["observationSequence"] for item in receipts] == [3, 4]
    assert all("previewBase64" in item["evidence"]["stdout"] for item in receipts)
    persisted = [json.loads(record["output"]) for record in evidence.records
                 if record["tool_name"] == "implementation_check"]
    current_persisted = persisted[-2:]
    for document in current_persisted:
        # Journals additionally retain full-output lookup capabilities. Those
        # diagnostics deliberately stay outside the native acceptance schema.
        outputs = document.pop("verificationOutputs")
        assert len(outputs) == 1
        assert set(outputs[0]["streams"]) == {"stdout", "stderr"}
        assert all(stream["storageComplete"] is True and stream["outputRef"]
                   for stream in outputs[0]["streams"].values())
    assert current_persisted == receipts, "Full current evidence must survive outside the displayed tool result"
    assert cap.completion_pending
    cap.begin_model_response(context=replace(context, _subagent_mailbox=None))
    assert cap.capture_checked_candidate(context=context).handoff_reason == "executor_write_feedback_pending"
    rejected = registry.execute("executor_submit", _submission(), context)
    assert not rejected.ok and not rejected._implementation_submitted
    assert "failed candidate writes" in rejected.output
    assert store.read(channel.write_key) is None
    assert budget.snapshot() == budget_before, "Checking does not create model calls or extend their budget"


def test_next_admitted_owner_response_receives_error_and_can_acknowledge_it(tmp_path, monkeypatch):
    cap, registry, context, store, channel, target, observed, evidence, budget = _case(tmp_path, monkeypatch)

    class Model:
        calls = 0

        def next(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                action = {"id": "failed-single-edit", "toolName": "edit_file", "input": FAILED_EDIT}
            else:
                assert self.calls == 2
                failure = next(item for item in messages if item.get("role") == "tool_result"
                               and item.get("toolUseId") == "failed-single-edit")
                assert failure["isError"] and "Search string not found" in failure["content"]
                assert FEEDBACK in failure["content"]
                assert not cap.completion_pending, "Only the newly admitted owner response acknowledges the error"
                snapshot = cap.capture_checked_candidate(context=context)
                assert snapshot is not None and snapshot.handoff_reason == "executor_budget_exceeded"
                action = {"id": "native-handoff", "toolName": "executor_submit", "input": _submission()}
            return AgentStep(type="tool_calls", calls=[action],
                             usage=ModelUsage(input_tokens=100, output_tokens=100, source="provider"))

    model = Model()
    messages = run_agent_turn(model=model, tools=registry,
        messages=[{"role": "user", "content": "Inspect the failed edit feedback, then decide whether to submit."}],
        cwd=str(tmp_path), subagent_mailbox=channel, agent_budget=budget,
        event_sink=evidence, deadline_monotonic=context._task_deadline_monotonic,
        implementation_task=True, enable_implementation_phases=False, enable_work_chain=False,
        max_steps=4)
    assert model.calls == budget.snapshot().used_model_calls == 2
    assert (budget.snapshot().limit_model_calls, budget.snapshot().limit_total_tokens) == (40, 500000)
    assert budget.snapshot().used_total_tokens == 400
    assert len(observed) == 2 and target.read_bytes() == b"value = 2\n"
    assert json.loads(store.read(channel.write_key).content)["status"] == "completed"
    handoff = next(item for item in messages if item.get("role") == "tool_result"
                   and item.get("toolUseId") == "native-handoff")
    assert not handoff["isError"] and json.loads(handoff["content"])["verification"] == "pending"


def test_failed_edit_with_expired_feedback_keeps_error_but_has_no_ready_candidate(tmp_path, monkeypatch):
    cap, registry, context, store, channel, target, observed, _, _ = _case(tmp_path, monkeypatch)

    def expired(_check, _context):
        raise VerificationDeadlineExceeded("actual feedback deadline")

    cap._run_check = expired
    result = registry.execute("edit_file", FAILED_EDIT, context)
    assert not result.ok and result._edit_anchor_failed and "Search string not found" in result.output
    assert result._implementation_checks_failed is None
    assert '"status": "inconclusive"' in result.output
    assert cap.capture_checked_candidate(context=context) is None
    assert target.read_bytes() == b"value = 2\n" and not observed
    assert store.read(channel.write_key) is None


def test_failed_edit_does_not_swallow_check_abandonment(tmp_path, monkeypatch):
    cap, registry, context, _, _, target, _, _, _ = _case(tmp_path, monkeypatch)

    def abandoned(_check, _context):
        raise ToolExecutionAbandoned("owner abandoned actual checks")

    cap._run_check = abandoned
    with pytest.raises(ToolExecutionAbandoned, match="owner abandoned"):
        registry.execute("edit_file", FAILED_EDIT, context)
    assert cap.capture_checked_candidate(context=context) is None
    assert target.read_bytes() == b"value = 2\n"


def test_owner_abandoned_after_failed_writer_starts_no_checks(tmp_path, monkeypatch):
    cap, _, context, store, channel, target, observed, evidence, _ = _case(tmp_path, monkeypatch)
    abandoned = threading.Event()
    writer_results = []

    def writer(value, owner_context):
        result = edit_file_tool.run(value, owner_context)
        assert not result.ok and result._edit_anchor_failed
        writer_results.append(result)
        abandoned.set()
        return result

    registry = ToolRegistry([cap.with_write_feedback(replace(edit_file_tool, run=writer))])
    with pytest.raises(ToolExecutionAbandoned):
        registry.execute("edit_file", FAILED_EDIT, replace(context, _tool_abandoned=abandoned))
    assert len(writer_results) == 1 and "Search string not found" in writer_results[0].output
    assert not observed, "The actual owner event must stop every check before its callback starts"
    assert not evidence.records
    assert target.read_bytes() == b"value = 2\n"
    assert cap.capture_checked_candidate(context=context) is None
    assert store.read(channel.write_key) is None


def test_expired_write_floor_does_not_invoke_writer_or_feedback(tmp_path, monkeypatch):
    cap, registry, context, _, _, target, observed, _, _ = _case(tmp_path, monkeypatch)
    cap.reserve_handoff_time(time.monotonic() - 1)
    result = registry.execute("edit_file", {"path": "candidate.py", "old": "value = 2", "new": "value = 3"}, context)
    assert not result.ok and "implementation_handoff_reserved" in result.output
    assert not observed and target.read_bytes() == b"value = 2\n"
    assert cap.capture_checked_candidate(context=context) is None
