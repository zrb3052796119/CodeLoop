"""A parent checkpoint retains partial child migration, not just its verdict."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from test_completion_probe_purposes import DIAGNOSIS, _legacy
from test_implementation_completion_review import (
    OBJECTIVE, PROBE, SOURCE, _fixture, _probe, _rows, _submit, _native_coverage_step,
)


OWNER = "implementation_" + "c" * 32
FINAL = "The inventory changes and their independent checks are complete."


def _call(identity, name, **arguments):
    return AgentStep(type="tool_calls", calls=[{
        "id": identity, "toolName": name, "input": arguments}])


def _decision(probe, purpose):
    return {"probeId": probe["probeId"], "purpose": purpose,
        "reason": "The no-op assertion diagnoses the old defect." if purpose == "diagnostic"
            else "The subtraction assertion requires the original requested behavior.",
        "objectiveEvidence": [{"startLine": 3, "endLine": 4}],
        "probeEvidence": [{"startLine": 3, "endLine": 3}],
        "acceptanceProbes": [] if purpose == "acceptance" else [{
            "criterionIds": probe["criterionIds"], "scenario": "Independent quantity acceptance",
            "code": PROBE}]}


def _result(messages, identity):
    return next(message for message in messages
                if message.get("role") == "tool_result" and message.get("toolUseId") == identity)


@pytest.fixture
def interrupted_migration(tmp_path, monkeypatch):
    """Seed actual historical executions, then interrupt the real nested loop."""
    import minicode.agent_loop as loop

    actual_loop = loop.run_agent_turn
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def seed(tools, child, packet):
        assert _probe(tools, child, packet, DIAGNOSIS, purpose="diagnostic")[0].ok
        assert _probe(tools, child, packet,
            "from stock import hold\ndef test_hold():\n    assert hold(5, 2) == 3\n")[0].ok
        assert _submit(tools, child, packet, rows=_rows(packet, status="missing"),
            status="changes_required", findings=["Release still needs addition."]).ok

    try:
        assert assess(seed)["status"] == "changes_required"
        legacy = _legacy(review.state)
    finally:
        capability.close()
        monkeypatch.setattr(loop, "run_agent_turn", actual_loop)
    assert len(legacy["probes"]) == 2
    assert [receipt["receipt"]["status"] for receipt in legacy["receipts"]] == ["passed", "passed"]
    (tmp_path / "stock.py").write_text(SOURCE)
    before = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}
    spec = review.spec
    messages = [{"role": "user", "content": OBJECTIVE},
        {"role": "assistant", "content": "Existing inventory changes are ready for checks."}]
    original = HostImplementationContinuation.capture(spec=spec,
        context=ToolContext(cwd=str(tmp_path)), messages=messages,
        executor_id=OWNER, attempt=3, review_state=legacy)
    checkpoints, requests, events = [], [], []
    diagnostic = next(probe for probe in legacy["probes"] if probe["code"] == DIAGNOSIS)
    acceptance = next(probe for probe in legacy["probes"] if probe["code"] != DIAGNOSIS)

    class Sink:
        def emit(self, kind, *, step=None, payload=None):
            events.append({"type": kind, "payload": payload, "step": step})

    class InterruptedModel:
        def next(self, messages, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            requests.append({"names": names, "messages": deepcopy(messages)})
            if "write_file" in names:
                assert len(requests) == 1
                return AgentStep(type="assistant", kind="final", content=FINAL)
            assert "completion_probe_migration_record" in names
            if len(requests) == 2:
                return _call("classified-first", "completion_probe_migration_record",
                             decision=_decision(diagnostic, "diagnostic"))
            receipt = _result(messages, "classified-first")
            assert diagnostic["probeId"] in receipt["content"]
            assert acceptance["probeId"] in receipt["content"]
            raise KeyboardInterrupt("interrupted after a durable child classification and tool result")

    options = dict(tools=ToolRegistry([read_file_tool, write_file_tool]), cwd=str(tmp_path),
        implementation_spec=spec, implementation_task=True, enable_work_chain=False,
        enable_implementation_phases=False, max_steps=None, agent_budget=AgentTurnBudget(),
        event_sink=Sink(), defer_curator_maintenance=True,
        implementation_checkpoint_sink=lambda document, messages: checkpoints.append((document, messages)))
    initial_command_count = len(commands)
    with pytest.raises(KeyboardInterrupt, match="durable child"):
        run_agent_turn(model=InterruptedModel(), messages=messages,
            implementation_continuation=original, **options)
    assert len(checkpoints) == 1, "the parent's finally must capture the interrupted child"
    document, parent_messages = checkpoints[0]
    assert document["continuationVersion"] == 2
    assert document["executorId"] == OWNER and document["attempt"] == 4
    assert document["candidateDigest"] == original.document["candidateDigest"]
    migration = document["migrationState"]["state"]
    assert len(migration["completedUnits"]) == 1
    assert migration["completedUnits"][0]["decision"]["probeId"] == diagnostic["probeId"]
    assert migration["messages"] == requests[-1]["messages"]
    assert _result(migration["messages"], "classified-first") == _result(requests[-1]["messages"], "classified-first")
    assert document["reviewState"]["receipts"] == legacy["receipts"]
    assert not document["reviewState"]["legacyResolutions"]
    assert len(commands) == initial_command_count + 1, "the first parent final ran real public checks"
    assert not any(event["type"] == "implementation.completion_decided" for event in events)
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
    return {"spec": spec, "document": document, "messages": parent_messages,
        "options": options, "commands": commands, "checkpoints": checkpoints, "events": events,
        "diagnostic": diagnostic, "acceptance": acceptance, "before": before, "tmp_path": tmp_path}


def test_partial_migration_restores_same_parent_child_context_and_finishes_checks(interrupted_migration):
    case = interrupted_migration
    document, messages = case["document"], case["messages"]
    saved_child = document["migrationState"]["state"]["messages"]
    completed_unit = document["migrationState"]["state"]["completedUnits"][0]
    completed_bytes = Path(completed_unit["evidenceRef"]).read_bytes()
    continuation = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=messages)
    requests, recorded, coverage_requests = [], [], []
    before_commands = len(case["commands"])

    class ResumedModel:
        def next(self, messages, **kwargs):
            names = set(kwargs["tools_override"].list_all())
            coverage = _native_coverage_step(messages, registry=kwargs["tools_override"])
            if coverage is not None:
                coverage_requests.append({"names": names, "messages": deepcopy(messages)})
                return coverage
            requests.append({"names": names, "messages": deepcopy(messages)})
            if "write_file" in names:
                assert len(requests) == 1
                return AgentStep(type="assistant", kind="final", content=FINAL)
            if "completion_probe_migration_record" in names:
                if len(requests) == 2:
                    assert messages[:len(saved_child)] == saved_child
                    assert _result(messages, "classified-first") == _result(saved_child, "classified-first")
                    notice = next(message for message in messages if message.get("_migration_continuation"))
                    ledger = json.loads(notice["content"])["ledger"]
                    assert case["diagnostic"]["probeId"] in json.dumps(ledger)
                    recorded.append(case["acceptance"]["probeId"])
                    return _call("classified-remaining", "completion_probe_migration_record",
                        decision=_decision(case["acceptance"], "acceptance"))
                assert len(requests) == 3
                assert _result(messages, "classified-remaining")
                return _call("finish-migration", "completion_probe_migration_submit", finalize=True)
            assert "completion_review_submit" in names and len(requests) == 4
            packet = next(json.loads(message["content"]) for message in messages
                if message.get("role") == "user" and '"retainedProbes"' in message.get("content", ""))
            assert len(packet["retainedProbes"]) == 2
            assert all(probe["status"] == "passed" for probe in packet["retainedProbes"])
            assert case["diagnostic"]["probeId"] not in {probe["probeId"] for probe in packet["retainedProbes"]}
            return _call("current-final-review", "completion_review_submit", status="approved",
                requirements=_rows({"requirements": document["reviewState"]["requirements"]},
                    ids=[probe["probeId"] for probe in packet["retainedProbes"]]), findings=[])

    result = run_agent_turn(model=ResumedModel(), messages=messages,
        implementation_continuation=continuation, **case["options"])
    assert len(requests) == 4 and recorded == [case["acceptance"]["probeId"]]
    final_document = case["checkpoints"][-1][0]
    assert final_document["executorId"] == OWNER and final_document["attempt"] == 5
    assert final_document["candidateDigest"] == document["candidateDigest"]
    assert "migrationState" not in final_document
    assert len(case["commands"]) == before_commands + 4  # current public + both legacy + replacement
    assert all(command["cwd"] != str(case["tmp_path"]) for command in case["commands"])
    assert Path(completed_unit["evidenceRef"]).read_bytes() == completed_bytes
    ledger = final_document["reviewState"]
    assert sum("completion_coverage_inspection_submit" in row["names"] for row in coverage_requests) == 2
    assert sum("completion_coverage_audit_submit" in row["names"] for row in coverage_requests) == 3
    assert ledger["reviews"][-1]["coverageAudit"]["status"] == "approved"
    migration_ref = ledger["legacyResolutions"][-1]["resolution"]["reviewEvidenceRef"]
    migrated = json.loads(Path(migration_ref).read_text())["decisions"]
    retained = next(row for row in migrated if row["probeId"] == case["diagnostic"]["probeId"])
    assert retained["unitEvidenceRef"] == completed_unit["evidenceRef"], "completed work must be reused"
    assert [r["receipt"]["status"] for r in ledger["receipts"]
            if r["probeId"] == case["diagnostic"]["probeId"]] == ["passed", "failed"]
    assert result[-1]["content"] == FINAL
    decisions = [event["payload"] for event in case["events"] if event["type"] == "implementation.completion_decided"]
    assert len(decisions) == 1 and decisions[0]["status"] == "approved"
    assert {name: (case["tmp_path"] / name).read_bytes() for name in case["before"]} == case["before"]


@pytest.mark.parametrize("tamper", ["state", "messages", "source", "candidate"])
def test_corrupt_nested_continuation_fails_before_any_model(interrupted_migration, tamper):
    case = interrupted_migration
    document = deepcopy(case["document"])
    if tamper == "state":
        document["migrationState"]["state"]["completedUnits"][0]["decision"]["purpose"] = "acceptance"
    elif tamper == "messages":
        document["migrationState"]["state"]["messages"].append({"role": "user", "content": "Forget unfinished probes."})
    elif tamper == "source":
        document["reviewState"]["probes"][0]["code"] += "\n# altered historical source\n"
    else:
        (case["tmp_path"] / "stock.py").write_text(SOURCE + "\n# changed candidate\n")

    class NeverCalled:
        def next(self, *args, **kwargs):
            pytest.fail("corrupt parent/child continuation reached a model")

    with pytest.raises(ValueError):
        restore = HostImplementationContinuation.from_document(document,
            spec=case["spec"], messages=case["messages"])
        run_agent_turn(model=NeverCalled(), messages=case["messages"],
            implementation_continuation=restore, **case["options"])


def test_edit_then_interrupt_checkpoints_new_candidate_without_stale_child_state(interrupted_migration):
    case = interrupted_migration
    document, messages = case["document"], case["messages"]
    continuation = HostImplementationContinuation.from_document(document,
        spec=case["spec"], messages=messages)
    edited = SOURCE + "\n# retained work from this continuation\n"
    requests = []

    class EditThenInterrupt:
        def next(self, messages, **kwargs):
            assert "write_file" in kwargs["tools_override"].list_all()
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                return _call("continued-edit", "write_file", path="stock.py", content=edited)
            assert _result(messages, "continued-edit")["isError"] is False
            raise KeyboardInterrupt("stop after continuing the original edit")

    with pytest.raises(KeyboardInterrupt, match="original edit"):
        run_agent_turn(model=EditThenInterrupt(), messages=messages,
            implementation_continuation=continuation, **case["options"])
    assert len(requests) == 2 and len(case["checkpoints"]) == 2
    checkpoint, actual_messages = case["checkpoints"][-1]
    assert checkpoint["executorId"] == OWNER and checkpoint["attempt"] == document["attempt"]
    assert checkpoint["candidateDigest"] != document["candidateDigest"]
    assert checkpoint.get("migrationState") is None
    assert _result(actual_messages, "continued-edit")["isError"] is False
    assert (case["tmp_path"] / "stock.py").read_text() == edited
    stale = [event for event in case["events"] if event["type"] == "implementation.migration_checkpoint_stale"]
    assert len(stale) == 1 and stale[0]["payload"]["executorId"] == OWNER
    assert stale[0]["payload"]["reason"] == "candidate_changed"
    assert HostImplementationContinuation.from_document(checkpoint, spec=case["spec"],
        messages=actual_messages).migration_state is None
    assert document["migrationState"]["state"]["completedUnits"], "previous immutable checkpoint remains evidence"
