"""Real parent/reviewer loops must not redispatch an old repair instruction."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.task_ledger import TaskLedger
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from test_implementation_completion_review import _native_coverage_step, _inspection_step


OBJECTIVE = "1. hold subtracts the amount.\n2. release adds the amount."
PARTIAL = "def hold(n, amount):\n    return n - amount\n\ndef release(n, amount):\n    return n\n"
COMPLETE = PARTIAL.replace("return n\n", "return n + amount\n")
PUBLIC = "from stock import hold\ndef test_hold():\n    assert hold(10, 3) == 7\n"
PROBE = ("from stock import hold, release\ndef test_both():\n"
         "    assert hold(10, 3) == 7\n    assert release(7, 3) == 10\n")
FINAL = "Both operations are implemented and independently tested."


def _call(identity, name, **arguments):
    return AgentStep(type="tool_calls", calls=[{
        "id": identity, "toolName": name, "input": arguments}])


def _result(messages, identity):
    return next(m for m in messages if m.get("role") == "tool_result"
                and m.get("toolUseId") == identity)


def _goal(messages):
    ledger = next(m["content"] for m in messages if m.get("_task_ledger"))
    return ledger.split("## Goal (verbatim, bounded)\n", 1)[1].split("\n\n##", 1)[0]


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner

    (tmp_path / "stock.py").write_text(PARTIAL, newline="\n")
    (tmp_path / "test_public.py").write_text(PUBLIC, newline="\n")
    commands, checkpoints, events, violations = [], [], [], []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **kwargs):
            commands.append({"argv": list(argv), "cwd": Path(cwd)})
            return argv

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
            explicit_trusted_executables={"python3": sys.executable}))
    # The parent and nested reviewer use the in-process driver exclusively.
    monkeypatch.setattr("minicode.openai_adapter.open_verified_url",
                        lambda *_a, **_k: pytest.fail("No provider call belongs in this regression"))
    spec = ImplementationSpec(("stock.py", "test_public.py"), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),
    ), OBJECTIVE)
    ids = [unit["id"] for unit in requirement_units(OBJECTIVE)]

    class Driver:
        model_id = "chronology-test"

        def __init__(self, steps):
            self.steps = iter(steps)
            self.requests = []
            self.all_requests = []
            self.probe_id = None
            self.review_calls = 0
            self.adjudication_calls = 0
            self.coverage_calls = 0

        def next(self, messages, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            self.all_requests.append(names)
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            if "completion_coverage_audit_submit" in names:
                self.coverage_calls += 1
                assert not {"write_file", "executor_check", "completion_review_submit"} & names
                step = _native_coverage_step(messages)
                assert step is not None
                return step
            if "completion_probe_adjudication_submit" in names:
                self.adjudication_calls += 1
                assert names == {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
                packet = next(json.loads(m["content"]) for m in messages
                    if m.get("role") == "user" and '"originalObjective"' in m.get("content", ""))
                assert packet["originalObjective"] == OBJECTIVE
                assert packet["probe"]["code"] == PROBE
                assert packet["receipt"]["receipt"]["status"] == "failed"
                if self.adjudication_calls == 1:
                    return _call("adjudication-source", "read_file", path="stock.py")
                assert self.adjudication_calls == 2
                assert _result(messages, "adjudication-source")["isError"] is False
                assert (tmp_path / "stock.py").read_text() == PARTIAL
                return _call("adjudication-product-gap", "completion_probe_adjudication_submit",
                    verdict="product_gap", reason="release returns the unchanged count instead of adding the amount.",
                    objectiveEvidence=[{"startLine": 2, "endLine": 2}],
                    sourceEvidence=[{"path": "stock.py", "startLine": 4, "endLine": 5}])
            if "completion_review_submit" in names:
                self.review_calls += 1
                if self.review_calls == 1:
                    return _call("acceptance-probe", "review_test", purpose="acceptance",
                        criterionIds=ids, scenario="Both inventory operations preserve quantity.", code=PROBE)
                if self.review_calls == 2:
                    evidence = json.JSONDecoder().raw_decode(_result(messages, "acceptance-probe")["content"])[0]
                    assert evidence["receipt"]["status"] == "failed"
                    self.probe_id = evidence["probeId"]
                    return self.verdict("changes_required")
                assert self.review_calls == 3
                packet = next(json.loads(m["content"]) for m in messages
                    if m.get("role") == "user" and '"retainedProbes"' in m.get("content", ""))
                assert packet["retainedProbes"] == [{"probeId": self.probe_id,
                    "criterionIds": sorted(ids), "status": "passed"}]
                return self.verdict("approved")
            assert {"write_file", "executor_check"} <= names
            self.requests.append(deepcopy(messages))
            if _goal(messages) != OBJECTIVE:
                violations.append("host metadata replaced original Task Ledger goal")
            step = next(self.steps)
            return step(messages) if callable(step) else step

        def verdict(self, status):
            return _call("verdict-" + status, "completion_review_submit", status=status,
                requirements=[{"id": identity,
                    "status": "covered" if status == "approved" else "missing",
                    "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                    "probeIds": [self.probe_id] if status == "approved" else [],
                    "rationale": "The independent probe verifies both operations." if status == "approved"
                        else "release has not implemented the requested addition."} for identity in ids],
                findings=[] if status == "approved" else ["release(7, 3) must add the amount."])

    def inspect_after(identity, then):
        def step(messages):
            feedback = [i for i, m in enumerate(messages) if m.get("_implementation_completion_feedback")]
            result_index = next(i for i, m in enumerate(messages)
                                if m.get("role") == "tool_result" and m.get("toolUseId") == identity)
            assert len(feedback) == 1 and OBJECTIVE in messages[feedback[0]]["content"]
            result = messages[result_index]
            assert result["isError"] is False
            if result.get("toolName") == "executor_check":
                receipt = json.loads(result["content"])
                assert receipt["status"] == "passed" and receipt["evidence"]["exitCode"] == 0
                assert "1 passed" in receipt["evidence"]["stdout"]["preview"]
            if feedback[0] >= result_index:
                violations.append(f"old feedback was moved after actual result {identity}")
            return then
        return step

    options = dict(tools=ToolRegistry([read_file_tool, write_file_tool]), cwd=str(tmp_path),
        runtime={"model": "chronology-test", "reflectionMode": "rule"},
        implementation_spec=spec, implementation_task=True, enable_work_chain=True,
        enable_implementation_phases=False, max_steps=None, agent_budget=AgentTurnBudget(),
        event_sink=Sink(), defer_curator_maintenance=True,
        implementation_checkpoint_sink=lambda document, messages: checkpoints.append((document, messages)))
    return {"spec": spec, "driver": Driver, "inspect": inspect_after, "options": options,
        "commands": commands, "events": events, "checkpoints": checkpoints, "violations": violations,
        "workspace": tmp_path}


@pytest.mark.parametrize("drop_feedback", [False, True], ids=["normal-history", "projection-omits-feedback"])
def test_rejected_final_preserves_feedback_before_real_check_repair_results(inventory, monkeypatch, drop_feedback):
    case = inventory
    dropped = []
    if drop_feedback:
        original = TaskLedger.reconcile

        def project(self, messages):
            result = original(self, messages)
            if (not dropped and any(m.get("toolUseId") == "before-repair" and m.get("role") == "tool_result"
                                    for m in result)):
                dropped.append(True)
                # Exercise the real loop's missing-context reconciliation seam;
                # no review, tool result or approval is replaced by this seam.
                result = [m for m in result if not m.get("_implementation_completion_feedback")]
            return result
        monkeypatch.setattr(TaskLedger, "reconcile", project)
    model = case["driver"]([
        AgentStep(type="assistant", kind="final", content="Ready for delivery."),
        _call("before-repair", "executor_check", check=1),
        case["inspect"]("before-repair", _call("repair", "write_file", path="stock.py", content=COMPLETE)),
        case["inspect"]("repair", _call("after-repair", "executor_check", check=1)),
        case["inspect"]("after-repair", AgentStep(type="assistant", kind="final", content=FINAL)),
    ])
    result = run_agent_turn(model=model, messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    decisions = [e["payload"] for e in case["events"] if e["type"] == "implementation.completion_decided"]
    assert [r["status"] for r in decisions] == ["changes_required", "approved"]
    assert len({r["executorId"] for r in decisions}) == 1
    assert [r["attempt"] for r in decisions] == [1, 2]
    assert model.review_calls == 3 and len(model.requests) == 5
    # The accepted source-backed omission already authorizes this same owner
    # to repair release; an unrelated attribution round adds no prerequisite.
    assert model.adjudication_calls == 0
    assert decisions[0]["repairProbes"] == []
    assert [row["source"]["probeId"] for row in decisions[0]["unresolvedProbes"]] == [model.probe_id]
    assert any(row["status"] == "missing" and row["sourceEvidence"]
               for row in decisions[0]["requirements"])
    assert sum("completion_coverage_inspection_submit" in names for names in model.all_requests) == 2
    assert len(model.all_requests) == len(model.requests) + model.review_calls + model.adjudication_calls + model.coverage_calls + 2
    assert model.coverage_calls == 2  # actual source read, then bound native audit submission
    assert result[-1]["content"] == FINAL
    assert (case["workspace"] / "stock.py").read_text() == COMPLETE
    assert case["commands"] and all(row["cwd"] != case["workspace"] for row in case["commands"])
    assert bool(dropped) is drop_feedback
    assert not case["violations"], case["violations"]


def test_old_checkpoint_feedback_cannot_override_goal_or_follow_new_results(inventory, monkeypatch):
    case = inventory

    def interrupt(messages):
        assert _result(messages, "before-interruption")["isError"] is False
        raise KeyboardInterrupt("retain actual review feedback and successful checks")

    model = case["driver"]([
        AgentStep(type="assistant", kind="final", content="Ready for delivery."),
        _call("before-interruption", "executor_check", check=1), interrupt,
    ])
    manager = ContextManager(model="chronology-test")
    original_reconcile = TaskLedger.reconcile

    def legacy_projection(self, messages):
        projected = original_reconcile(self, messages)
        feedback = [m for m in projected if m.get("_implementation_completion_feedback")]
        return [m for m in projected if not m.get("_implementation_completion_feedback")] + feedback

    # Produce an actual old-order host checkpoint through the real parent loop,
    # rather than assuming the checkpoint was written by the repaired version.
    with monkeypatch.context() as legacy:
        legacy.setattr(TaskLedger, "reconcile", legacy_projection)
        with pytest.raises(KeyboardInterrupt, match="retain actual review feedback"):
            run_agent_turn(model=model, messages=[{"role": "user", "content": OBJECTIVE}],
                context_manager=manager, **case["options"])
    document, saved = case["checkpoints"][-1]
    assert document["completionFeedback"] in saved and saved == manager.messages
    assert saved.index(document["completionFeedback"]) > saved.index(_result(saved, "before-interruption"))
    restore = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=saved)
    prior_receipt = deepcopy(_result(saved, "before-interruption"))

    def first_resumed(messages):
        assert _result(messages, "before-interruption") == prior_receipt
        return _call("resumed-check", "executor_check", check=1)

    model.steps = iter([
        first_resumed,
        case["inspect"]("resumed-check", _call("resumed-repair", "write_file", path="stock.py", content=COMPLETE)),
        case["inspect"]("resumed-repair", _call("final-check", "executor_check", check=1)),
        case["inspect"]("final-check", AgentStep(type="assistant", kind="final", content=FINAL)),
    ])
    result = run_agent_turn(model=model, messages=saved, implementation_continuation=restore, **case["options"])
    assert result[-1]["content"] == FINAL
    assert case["checkpoints"][-1][0]["executorId"] == document["executorId"]
    assert case["checkpoints"][-1][0]["attempt"] == document["attempt"] + 1
    assert case["checkpoints"][-1][0]["candidateDigest"] != document["candidateDigest"]
    # Restoring the accepted omission preserves repair authority and ordering
    # without re-entering attribution before the original owner's next check.
    assert model.adjudication_calls == 0
    assert sum("completion_coverage_inspection_submit" in names for names in model.all_requests) == 2
    assert len(model.all_requests) == len(model.requests) + model.review_calls + model.adjudication_calls + model.coverage_calls + 2
    assert model.coverage_calls == 2
    assert [e["payload"]["status"] for e in case["events"]
            if e["type"] == "implementation.completion_decided"] == ["changes_required", "approved"]
    assert not case["violations"], case["violations"]


@pytest.mark.parametrize("host_flag", ["_implementation_completion_feedback", "_model_transport_failure",
                                      "_migration_continuation", "_implementation_context"])
def test_host_user_packets_do_not_become_real_loop_task_goal(tmp_path, host_flag):
    seen = []

    class Model:
        def next(self, messages, **kwargs):
            seen.append(_goal(messages))
            return AgentStep(type="assistant", kind="final", content="Observed the original goal.")

    run_agent_turn(model=Model(), tools=ToolRegistry([]), cwd=str(tmp_path),
        messages=[{"role": "user", "content": OBJECTIVE},
                  {"role": "user", host_flag: True, "content": "Host state: retry check 1."}],
        enable_work_chain=False, defer_curator_maintenance=True)
    assert seen == [OBJECTIVE]
