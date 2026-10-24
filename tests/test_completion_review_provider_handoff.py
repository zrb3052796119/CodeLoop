"""Real adapter/parent/reviewer transitions preserve one implementation owner."""

import io
import json
from pathlib import Path
import sys

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_execution import ImplementationSpec
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from test_implementation_completion_review import _coverage_approval
from test_completion_coverage_audit import _inspection_action


def test_real_provider_rejection_returns_to_same_executor_then_replays_probe_and_delivers(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner

    objective = "1. hold subtracts amount from available.\n2. release adds amount to available."
    initial = ("def hold(available, amount):\n    return available\n\n"
               "def release(available, amount):\n    return available\n")
    partial_test = "from stock import hold\ndef test_hold():\n    assert hold(10, 3) == 7\n"
    complete_test = partial_test + "\nfrom stock import release\ndef test_release():\n    assert release(7, 3) == 10\n"
    probe_source = ("from stock import hold, release\ndef test_both():\n"
                    "    assert hold(10, 3) == 7\n    assert release(7, 3) == 10\n")
    (tmp_path / "stock.py").write_text(initial)
    (tmp_path / "test_requested.py").write_text("def test_initial():\n    assert True\n")
    commands, requests, events, presented, streamed = [], [], [], [], []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **kwargs):
            commands.append({"argv": list(argv), "cwd": Path(cwd)})
            return argv

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            events.append({"type": event_type, "step": step, "payload": payload})

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
            explicit_trusted_executables={"python3": sys.executable}))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    registry = ToolRegistry([read_file_tool, edit_file_tool, write_file_tool])
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
               "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    adapter = OpenAIModelAdapter(runtime, registry)
    original_schemas = json.loads(json.dumps(adapter.request_tool_schemas()))
    parent_names = {"read_file", "edit_file", "write_file", "executor_check", "executor_test", "verification_output_read", "completion_evidence_read"}
    reviewer_names = {"read_file", "review_test", "review_probe_read", "completion_review_submit", "completion_evidence_read"}
    adjudicator_names = {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
    auditor_names = reviewer_names - {"completion_review_submit"} | {
        "completion_coverage_audit_submit", "review_evidence_read"}
    requirement_ids = [unit["id"] for unit in requirement_units(objective)]
    parent_calls = reviewer_calls = adjudicator_calls = auditor_calls = 0
    failed_probe = {}
    premature = "Both operations are complete."
    final = "Both operations are implemented and tested."

    class Response(io.BytesIO):
        status = 200

    def call(identity, name, **arguments):
        return {"id": identity, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}

    def verdict(status):
        return {"status": status, "requirements": [
            {"id": identity, "status": "covered" if status == "approved" else "unknown",
             "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}] if status == "approved" else [],
             "probeIds": [failed_probe["probeId"]] if status == "approved" else [],
             "rationale": "Both public operations passed the retained probe." if status == "approved"
                else "The combined acceptance probe fails because release remains a no-op."}
            for identity in requirement_ids], "findings": [] if status == "approved" else [
                "release(7, 3) returns 7; the original addition requirement is missing."]}

    def transport(request, *, timeout):
        nonlocal parent_calls, reviewer_calls, adjudicator_calls, auditor_calls
        del timeout
        body = json.loads(request.data.decode())
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        # Classification and every chosen tool are based on the actual HTTP
        # schema, never a scripted Model.next that ignores the capabilities.
        role = ("adjudicator" if "completion_probe_adjudication_submit" in names
                else "auditor" if "completion_coverage_audit_submit" in names
                else "inspector" if "completion_coverage_inspection_submit" in names
                else "reviewer" if "completion_review_submit" in names else "parent")
        requests.append({"role": role, "body": body})
        tools, text = [], None
        if role == "inspector":
            action = _inspection_action(body)
            assert action is not None and not {"write_file", "edit_file", "executor_check"} & names
            tools = [call("inspection-" + str(len(requests)), action[0], **action[1])]
        elif role == "auditor":
            assert names == auditor_names
            auditor_calls += 1
            packet = next(json.loads(m["content"]) for m in body["messages"]
                          if m.get("role") == "user" and '"evidenceIndex"' in m.get("content", ""))
            if auditor_calls == 1:
                assert not presented and not streamed
                tools = [call("audit-source", "review_probe_read", probeId=failed_probe["probeId"],
                              receiptView="current", limit=16000)]
            else:
                assert auditor_calls == 2
                raw = next(m["content"] for m in body["messages"]
                           if m.get("role") == "tool" and m.get("tool_call_id") == "audit-source")
                source = json.loads(json.loads(raw)["text"])["source"]
                assert source["code"] == probe_source
                tools = [call("audit-approved", "completion_coverage_audit_submit",
                              **_coverage_approval(packet, {source["probeId"]: source}))]
        elif role == "adjudicator":
            assert names == adjudicator_names
            schema = next(tool["function"]["parameters"] for tool in body["tools"]
                          if tool["function"]["name"] == "completion_probe_adjudication_submit")
            assert set(schema["required"]) == {"verdict", "reason", "objectiveEvidence", "sourceEvidence"}
            assert set(schema["properties"]["verdict"]["enum"]) == {"product_gap", "probe_error", "inconclusive"}
            packet = next(json.loads(message["content"]) for message in body["messages"]
                if message.get("role") == "user" and '"originalObjective"' in message.get("content", ""))
            assert packet["originalObjective"] == objective
            assert packet["probe"]["code"] == probe_source
            assert packet["probe"]["probeId"] == failed_probe["probeId"]
            assert packet["receipt"]["receipt"]["status"] == "failed"
            adjudicator_calls += 1
            if adjudicator_calls == 1:
                tools = [call("adjudication-source", "read_file", path="stock.py")]
            else:
                assert adjudicator_calls == 2
                source = next(message["content"] for message in body["messages"]
                    if message.get("role") == "tool" and message.get("tool_call_id") == "adjudication-source")
                assert "return available" in source and "return available + amount" not in source
                tools = [call("adjudication-product-gap", "completion_probe_adjudication_submit",
                    verdict="product_gap", reason="release returns the unchanged count instead of the required addition.",
                    objectiveEvidence=[{"startLine": 2, "endLine": 2}],
                    sourceEvidence=[{"path": "stock.py", "startLine": 4, "endLine": 5}])]
        elif role == "parent":
            assert names == parent_names
            parent_calls += 1
            if parent_calls == 1:
                tools = [call("implement-hold", "edit_file", path="stock.py",
                    old="def hold(available, amount):\n    return available",
                    new="def hold(available, amount):\n    return available - amount"),
                    call("test-only-hold", "write_file", path="test_requested.py", content=partial_test)]
            elif parent_calls == 2:
                text = premature
            elif parent_calls == 3:
                # The same owner receives all previous edits and tool receipts,
                # plus actionable probe source rather than a temp-only ref.
                assert (tmp_path / "stock.py").read_text() == initial.replace(
                    "def hold(available, amount):\n    return available",
                    "def hold(available, amount):\n    return available - amount")
                assert (tmp_path / "test_requested.py").read_text() == partial_test
                assert {"implement-hold", "test-only-hold"} <= {
                    message.get("tool_call_id") for message in body["messages"] if message.get("role") == "tool"}
                feedback = next(message["content"] for message in reversed(body["messages"])
                    if "\nCompletion evidence:\n" in message.get("content", ""))
                decision = json.loads(feedback.split("\nCompletion evidence:\n", 1)[1])
                assert decision["continuesSameExecutor"] and decision["status"] == "changes_required"
                assert decision["repairProbes"][0]["code"] == probe_source
                wrapper = decision["probeReceipts"][0]
                assert wrapper.get("receipt", wrapper)["status"] == "failed"
                assert decision["probeReceipts"][0]["probeId"] == failed_probe["probeId"]
                assert not presented and not streamed
                tools = [call("complete-release", "edit_file", path="stock.py",
                    old="def release(available, amount):\n    return available",
                    new="def release(available, amount):\n    return available + amount"),
                    call("test-both", "write_file", path="test_requested.py", content=complete_test)]
            else:
                assert parent_calls == 4, "a new parent session or extra final request is a regression"
                text = final
        else:
            assert names == reviewer_names
            probe_schema = next(tool["function"]["parameters"] for tool in body["tools"]
                                if tool["function"]["name"] == "review_test")
            assert "purpose" in probe_schema["required"]
            assert set(probe_schema["properties"]["purpose"]["enum"]) == {"diagnostic", "acceptance"}
            reviewer_calls += 1
            if reviewer_calls == 1:
                tools = [call("independent-check", "review_test", purpose="acceptance", criterionIds=requirement_ids,
                    scenario="Both original quantity operations are exercised.", code=probe_source)]
            elif reviewer_calls == 2:
                receipt = json.JSONDecoder().raw_decode(next(message["content"] for message in reversed(body["messages"])
                    if message.get("role") == "tool"))[0]
                assert receipt["receipt"]["status"] == "failed"
                assert receipt["receipt"]["testReport"]["failed"] == 1
                failed_probe.update(receipt)
                # Probe attribution is this test's feedback contract. A native
                # source-backed missing row can return without that extra work.
                tools = [call("reject-gap", "completion_review_submit", **verdict("inconclusive"))]
            else:
                assert reviewer_calls == 3, "retained probe should replay without a new model-authored test"
                prompt = next(json.loads(message["content"]) for message in body["messages"]
                    if message.get("role") == "user" and '"retainedProbes"' in message.get("content", ""))
                assert prompt["retainedProbes"] == [{"probeId": failed_probe["probeId"],
                    "criterionIds": requirement_ids, "status": "passed"}]
                assert prompt["candidateDigest"] != failed_probe["candidateDigest"]
                tools = [call("approve-repair", "completion_review_submit", **verdict("approved"))]
        assert all(tool["function"]["name"] in names for tool in tools)
        message = {"role": "assistant", "content": text}
        if tools:
            message["tool_calls"] = tools
        return Response(json.dumps({"choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if tools else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    spec = ImplementationSpec(("stock.py", "test_requested.py"), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_requested.py"), 20),
    ), objective)
    capture = AgentOutcomeCapture()
    budget = AgentTurnBudget(max_model_calls=None, max_total_tokens=None, max_cost_usd=None)
    transcript = run_agent_turn(model=adapter, tools=registry, runtime=runtime,
        messages=[{"role": "user", "content": objective}], cwd=str(tmp_path),
        implementation_task=True, implementation_spec=spec, enable_work_chain=False,
        enable_implementation_phases=False, max_steps=None, agent_budget=budget,
        event_sink=Sink(), outcome_capture=capture, on_assistant_message=presented.append,
        on_assistant_stream_chunk=streamed.append, defer_curator_maintenance=True)

    assert [r["role"] for r in requests] == ["parent", "parent", "reviewer", "reviewer",
        "adjudicator", "adjudicator", "parent", "parent", "reviewer", "inspector", "inspector", "auditor", "auditor"]
    assert (parent_calls, reviewer_calls, adjudicator_calls) == (4, 3, 2)
    decisions = [e["payload"] for e in events if e["type"] == "implementation.completion_decided"]
    assert [d["status"] for d in decisions] == ["changes_required", "approved"]
    assert len({d["executorId"] for d in decisions}) == 1
    assert [d["attempt"] for d in decisions] == [1, 2]
    assert [d["probeReceipts"][0]["receipt"]["status"] for d in decisions] == ["failed", "passed"]
    assert len({d["probeReceipts"][0]["probeId"] for d in decisions}) == 1
    assert len({d["patchDigest"] for d in decisions}) == 2
    assert capture.outcome.status == "success" and capture.outcome.verification_status == "verified"
    assert presented == [final] and streamed == []
    assert transcript[-1]["content"] == final
    assert any(m.get("toolUseId") == "implement-hold" for m in transcript)
    assert len(commands) == 4 and all(c["cwd"] != tmp_path and not c["cwd"].exists() for c in commands)
    assert (tmp_path / "test_requested.py").read_text() == complete_test
    assert adapter.tools is registry and adapter.request_tool_schemas() == original_schemas
    assert budget.snapshot().used_model_calls == 13
