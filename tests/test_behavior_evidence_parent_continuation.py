"""Real local parent/child loops and pytest; only model decisions are scripted.

An accepted native nonapproval must return a behavioral evidence gap to the
existing implementer instead of trapping the independent auditor in retries.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from test_completion_feedback_chronology import _call, _result
from test_implementation_completion_review import _native_coverage_step, _inspection_step


OBJECTIVE = (
    "The real `release` must call `Store.save` to add a positive amount "
    "and reject a negative amount without changing the store."
)
PARTIAL = '''class Store:
    def __init__(self):
        self.count = 0
    def save(self, count):
        self.count = count
        return count

def normalize(count):
    return count

def release(store, count, amount):
    return store.save(count + amount)
'''
COMPLETE = PARTIAL.replace("    return store.save(count + amount)",
    "    if amount < 0:\n        raise ValueError('negative amount')\n    return store.save(count + amount)")
PUBLIC = "from stock import normalize\ndef test_smoke():\n    assert normalize(5) == 5\n"
HELPER = "from stock import normalize\ndef test_helper():\n    assert normalize(5) == 5\n"
POSITIVE = "from stock import Store, release\ndef test_positive():\n    assert release(Store(), 4, 3) == 7\n"
FULL = '''import pytest
from stock import Store, release
def test_release_contract():
    store = Store()
    assert release(store, 4, 3) == 7
    assert store.count == 7
    with pytest.raises(ValueError):
        release(store, 7, -1)
    assert store.count == 7
'''
RETAINED = "\n# Existing implementation work must survive a coverage nonapproval.\n"


def packet(messages, marker):
    return next(json.loads(message["content"]) for message in messages
        if message.get("role") == "user" and marker in message.get("content", ""))


@pytest.fixture
def api_store(tmp_path, monkeypatch):
    import minicode.workflow_runner as workflow

    # Two complete evidence audits exercise same-owner repair continuity;
    # their scripted reconciliation exceeds the separate bounded-mode cap.
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "full")
    commands, events, checkpoints = [], [], []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append({"argv": list(argv), "cwd": Path(cwd)})
            return argv

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append({"type": event_type, "payload": deepcopy(payload)})

    def no_network(*_args, **_kwargs):
        pytest.fail("Local behavior-continuation tests cannot contact a model provider")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(workflow, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
            explicit_trusted_executables={"python3": sys.executable}))
    (tmp_path / "test_public.py").write_text(PUBLIC)
    spec = ImplementationSpec(("stock.py", "test_public.py"), (VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),), OBJECTIVE)
    options = dict(tools=ToolRegistry([read_file_tool, write_file_tool]), cwd=str(tmp_path),
        runtime={"model": "local-behavior-continuation", "reflectionMode": "rule"},
        implementation_spec=spec, implementation_task=True, enable_work_chain=True,
        enable_implementation_phases=False, max_steps=None, agent_budget=AgentTurnBudget(),
        event_sink=Sink(), defer_curator_maintenance=True,
        implementation_checkpoint_sink=lambda document, messages: checkpoints.append(
            (deepcopy(document), deepcopy(messages))))
    return tmp_path, options, commands, events, checkpoints


@pytest.mark.parametrize("gap", ["helper-only", "omitted-sub-obligation"])
def test_behavior_gap_returns_to_original_owner_for_real_repair_and_delivery(api_store, gap):
    workspace, options, commands, events, checkpoints = api_store
    original_source = (COMPLETE if gap == "helper-only" else PARTIAL) + RETAINED
    final_source = COMPLETE + RETAINED
    weak_probe = HELPER if gap == "helper-only" else POSITIVE
    (workspace / "stock.py").write_text(original_source)
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    assert len(ids) == 1, "The regression must exercise several clauses within one original requirement."
    sequence, proposals, native_probes, first_parent_results = [], [], [], {}

    class Model:
        model_id = "local-behavior-continuation"
        parent_calls = reviewer_calls = calls = 0
        repair_started = False

        def next(self, messages, **kwargs):
            self.calls += 1
            if self.calls > 60:
                raise KeyboardInterrupt("Local scripted protocol did not reach its parent continuation")
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs["tools_override"].list_all())
            if "completion_coverage_audit_submit" in names:
                sequence.append("auditor")
                assert not {"write_file", "edit_file", "executor_check", "completion_review_submit"} & names
                current = packet(messages, '"evidenceIndex"')
                assert current["behaviorContract"]["originalObjective"] == OBJECTIVE
                children = current["behaviorContract"]["obligations"]
                assert len(children) == 2 and {child["parentId"] for child in children} == set(ids)
                required_symbols = [item["name"] for child in children for item in child["requiredSymbols"]]
                assert required_symbols == ["release", "Store.save"]
                step = _native_coverage_step(messages, stable_evidence=True)
                if step.calls[0]["toolName"] != "completion_coverage_audit_submit":
                    return step
                probe_id = current["proposal"]["requirements"][0]["probeIds"][0]
                repaired = self.repair_started
                first_line = 5 if repaired else 3

                def witness(line):
                    return {"probeId": probe_id, "startLine": line, "endLine": line}

                accounted = children if repaired or gap == "helper-only" else children[:1]
                row = {"id": ids[0], "status": "supported",
                    "reason": "The local script proposes coverage; the host must check the actual executed evidence.",
                    "witnesses": [witness(first_line)], "obligations": [
                        {"id": child["id"], "reason": (
                            "A positive release persists seven through the actual Store.save call." if n == 0 else
                            "A negative release raises ValueError and the already persisted count remains seven."),
                         "witnesses": [witness(9 if repaired and n == 1 else first_line)]}
                        for n, child in enumerate(accounted)]}
                step.calls[0]["input"] = {"status": "approved", "requirements": [row], "findings": []}
                step.calls[0]["id"] = "complete-evidence" if repaired else "insufficient-evidence"
                proposals.append(deepcopy(step.calls[0]["input"]))
                sequence.append("audit-submit-complete" if repaired else "audit-submit-insufficient")
                return step
            if "completion_review_submit" in names:
                sequence.append("reviewer")
                self.reviewer_calls += 1
                phase = (self.reviewer_calls - 1) // 2
                assert phase in (0, 1)
                assert phase == 0 or self.repair_started
                identity = "strong-probe" if phase else "weak-probe"
                if self.reviewer_calls % 2:
                    return _call(identity, "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="Real release behavior through the repository's Store.save, including negative rejection."
                            if phase else "Proposed initial inventory coverage.",
                        code=FULL if phase else weak_probe)
                receipt = json.JSONDecoder().raw_decode(_result(messages, identity)["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                assert receipt["receipt"]["testReport"].get("executionReport") is not None
                native_probes.append(deepcopy(receipt))
                return _call("strong-proposal" if phase else "weak-proposal", "completion_review_submit",
                    status="approved", findings=[], requirements=[{"id": ids[0], "status": "covered",
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1,
                            "endLine": len((final_source if phase else original_source).splitlines())}],
                        "probeIds": [receipt["probeId"]], "rationale": "The proposed independent pytest passed."}])
            assert {"write_file", "executor_check"} <= names
            self.parent_calls += 1
            sequence.append("parent")
            if self.parent_calls == 1:
                return _call("existing-source", "read_file", path="stock.py")
            if self.parent_calls == 2:
                first_parent_results["read"] = deepcopy(_result(messages, "existing-source"))
                return _call("existing-edit", "write_file", path="stock.py", content=original_source)
            if self.parent_calls == 3:
                first_parent_results["edit"] = deepcopy(_result(messages, "existing-edit"))
                return AgentStep(type="assistant", kind="final", content="The requested inventory behavior is complete.")
            assert _result(messages, "existing-source") == first_parent_results["read"]
            assert _result(messages, "existing-edit") == first_parent_results["edit"]
            if self.parent_calls == 4:
                assert sequence[-2] == "audit-submit-insufficient", "A semantic gap must exit the auditor immediately."
                feedback = next(m for m in messages if m.get("_implementation_completion_feedback"))
                receipt = json.loads(feedback["content"].split("\nCompletion evidence:\n", 1)[1])
                assert receipt["status"] == "changes_required" and receipt["continuesSameExecutor"]
                assert receipt["coverageAudit"]["status"] == "changes_required"
                text = " ".join(receipt["findings"])
                assert ("invocation path" in text if gap == "helper-only" else "sub-obligation" in text)
                assert (workspace / "stock.py").read_text() == original_source
                self.repair_started = True
                if gap == "omitted-sub-obligation":
                    return _call("same-owner-repair", "write_file", path="stock.py", content=final_source)
                return _call("same-owner-tests", "write_file", path="test_public.py", content=PUBLIC + "\n" + FULL)
            if gap == "omitted-sub-obligation" and self.parent_calls == 5:
                assert _result(messages, "same-owner-repair")["isError"] is False
                return _call("same-owner-tests", "write_file", path="test_public.py", content=PUBLIC + "\n" + FULL)
            check_turn = 6 if gap == "omitted-sub-obligation" else 5
            if self.parent_calls == check_turn:
                assert _result(messages, "same-owner-tests")["isError"] is False
                return _call("same-owner-check", "executor_check", check=1)
            assert self.parent_calls == check_turn + 1
            checked = _result(messages, "same-owner-check")
            assert checked["isError"] is False
            report = json.loads(checked["content"])
            assert report["status"] == "passed" and report["evidence"]["exitCode"] == 0
            assert "2 passed" in report["evidence"]["stdout"]["preview"]
            return AgentStep(type="assistant", kind="final", content="The real API path and rejection case are verified.")

    model = Model()
    messages = run_agent_turn(model=model, messages=[{"role": "user", "content": OBJECTIVE}], **options)
    assert messages[-1]["role"] == "assistant" and "verified" in messages[-1]["content"]
    assert len(proposals) == len(native_probes) == 2
    assert model.reviewer_calls == 4
    assert (workspace / "stock.py").read_text() == final_source
    assert FULL in (workspace / "test_public.py").read_text()
    assert _result(messages, "existing-edit") == first_parent_results["edit"]
    decisions = [event["payload"] for event in events if event["type"] == "implementation.completion_decided"]
    assert [row["status"] for row in decisions] == ["changes_required", "approved"]
    assert len({row["executorId"] for row in decisions}) == 1
    assert decisions[0]["continuesSameExecutor"] is True
    document = checkpoints[-1][0]
    assert document["executorId"] == decisions[0]["executorId"]
    assert document["reviewState"]["reviews"][-1]["status"] == "approved"
    assert not any(event["type"].startswith("subagent.") for event in events)
    assert any("test_public.py" in command["argv"] for command in commands)
    assert any(any("__minicode_review_probe__.py" in part for part in command["argv"]) for command in commands)
    evidence_dir = Path(document["reviewState"]["evidenceDirectory"])
    submissions = [json.loads(path.read_bytes()) for path in sorted(evidence_dir.glob("*-coverage-audit-tool-evidence.json"))]
    submitted = [item for item in submissions if item["toolName"] == "completion_coverage_audit_submit"
        and item["phase"] == "finished"]
    assert len(submitted) == 2 and all(item["isError"] is False for item in submitted)
    assert [json.loads(item["output"])["status"] for item in submitted] == ["changes_required", "approved"]
