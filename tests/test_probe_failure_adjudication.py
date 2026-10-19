"""Failure attribution uses a real provider schema and retained pytest evidence."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_execution import ImplementationSpec, implementation_candidate_digest
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.probe_failure_adjudication import ProbeFailureAdjudicator, validate_adjudication_record
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


OBJECTIVE = "release adds the supplied amount to stock."
SOURCE = "def release(stock, amount):\n    return stock + amount\n"
WRONG = "from stock import release\ndef test_release():\n    assert release(8, 4) == 4\n"
RIGHT = WRONG.replace("== 4", "== 12")


def _case(tmp_path, monkeypatch, *, product_gap=False, objective=OBJECTIVE, source=SOURCE,
          wrong=WRONG, right=RIGHT, scenario="The requested release operation increases stock."):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "stock.py").write_text(source.replace("stock + amount", "stock") if product_gap else source)
    public = "def test_public():\n    assert True\n"
    (root / "test_public.py").write_text(public)
    check = VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20)
    spec = ImplementationSpec(("stock.py", "test_public.py"), (check,), objective)
    probe = ReviewerProbe.create(criterion_ids=[u["id"] for u in requirement_units(objective)],
        scenario=scenario, code=right if product_gap else wrong)
    context = ToolContext(cwd=str(root))
    commands = []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append({"argv": list(argv), "cwd": str(cwd)})
            return argv

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    verifier = DeterministicVerifier(sandbox_backend=Sandbox(),
        explicit_trusted_executables={"python3": sys.executable})
    with WorkflowWorkspaceTransaction(str(root)) as transaction:
        frozen = transaction.freeze(allowed_paths=spec.write_paths, paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen, check=check,
            verifier=verifier, test_path="test_public.py", read_roots=(".",), forbidden_paths=(".git", ".env"),
            run_kwargs={"workflow_id": "wf_" + "a" * 32, "task_id": "task_attribution",
                "execution_assignment_id": "asg_attribution", "patch_digest": frozen.patch_digest,
                "permissions": PermissionManager(str(root), prompt=None), "permission_cwd": str(root)})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
    assert receipt["status"] == "failed" and receipt["testReport"]["failed"] == 1
    assert receipt["testReport"]["caseReport"]["cases"][0]["failureKind"] == "assertion"
    source_ref = tmp_path / "probe-source.json"
    source_ref.write_text(json.dumps(probe.to_dict()))
    wrapped = {"probeId": probe.probe_id, "candidateDigest": implementation_candidate_digest(spec, context),
               "sourceRef": str(source_ref), "receipt": receipt}
    receipt_ref = tmp_path / "probe-receipt.json"
    receipt_ref.write_text(json.dumps(wrapped))
    wrapped["receiptRef"] = str(receipt_ref)
    value = {"verdict": "product_gap" if product_gap else "probe_error",
        "reason": "The public objective and consumer source determine the required addition.",
        "objectiveEvidence": [{"startLine": 1, "endLine": 1}],
        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 2}]}
    if not product_gap:
        value["replacement"] = {"criterionIds": list(probe.criterion_ids), "scenario": probe.scenario, "code": right}
    return spec, probe, wrapped, context, value, commands


def _assess(case, model, *, tools=None, budget=None, sink=None, owner=None):
    spec, probe, receipt, context, _value, _commands = case
    return (owner or ProbeFailureAdjudicator(spec)).assess(probe=probe.to_dict(), receipt=receipt,
        context=context, model=model, tools=tools or ToolRegistry([read_file_tool, write_file_tool]),
        agent_budget=budget, event_sink=sink, cancellation_token=None, deadline_monotonic=None)


@pytest.mark.parametrize("runtime_override", [False, True])
def test_real_adapter_rejects_bad_anchor_then_submits_readonly_bound_judgment(tmp_path, monkeypatch, runtime_override):
    case = _case(tmp_path, monkeypatch)
    spec, probe, receipt, context, valid, commands = case
    parent = ToolRegistry([read_file_tool, write_file_tool])
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
               "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    if runtime_override:
        runtime["_openrouter_params"] = {"tools": [{"type": "function", "function": {
            "name": "parent_write", "parameters": {"type": "object", "properties": {}}}}],
            "tool_choice": {"type": "function", "function": {"name": "parent_write"}}}
    context._runtime = runtime
    adapter = OpenAIModelAdapter(runtime, parent)
    initial_schema = deepcopy(adapter.request_tool_schemas())
    original = {p.name: p.read_bytes() for p in Path(context.cwd).iterdir()}
    requests, events = [], []

    class Response(io.BytesIO):
        status = 200

    class Sink:
        def emit(self, name, *, step=None, payload=None):
            events.append((name, payload))

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        assert len(requests) <= 2, "A native accepted judgment must close its nested loop."
        assert {t["function"]["name"] for t in body["tools"]} == {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
        assert "tool_choice" not in body
        assert body["model"] == "gpt-test"
        assert OBJECTIVE in body["messages"][0]["content"]
        value = deepcopy(valid)
        if len(requests) == 1:
            value["sourceEvidence"][0]["endLine"] = 999
        else:
            output = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "tool")
            assert "probe_adjudication_invalid" in output and "actual source" in output
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"attribution-{len(requests)}",
            "type": "function", "function": {"name": "completion_probe_adjudication_submit", "arguments": json.dumps(value)}}]}
        return Response(json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    budget = AgentTurnBudget()
    result = _assess(case, adapter, tools=parent, budget=budget, sink=Sink())
    assert result["verdict"] == "probe_error" and "approved" not in result.values()
    assert result["correctionMode"] == "expected_literal"
    assert result["receiptRef"] == receipt["receiptRef"]
    assert result["candidateDigest"] == implementation_candidate_digest(spec, context)
    assert result["sourceDigest"] == probe.source_digest
    assert validate_adjudication_record(spec, probe.to_dict(), receipt, result) == result
    authority = json.loads(Path(result["authorityRef"]).read_text())
    assert authority["originalObjective"] == OBJECTIVE and authority["receipt"] == receipt
    assert authority["probe"] == probe.to_dict()
    assert result["sourceEvidence"][0]["lineDigest"].startswith("sha256:")
    assert {p.name: p.read_bytes() for p in Path(context.cwd).iterdir()} == original
    assert len(commands) == 1, "Attribution must not execute a replacement or mutate candidate tests."
    assert budget.snapshot().used_model_calls == 2 and budget.snapshot().limit_total_tokens is None
    assert adapter.tools is parent and adapter.request_tool_schemas() == initial_schema
    assert events and all(name == "implementation.completion_review_activity" for name, _ in events)
    assert all(payload["role"] == "completion_reviewer" for _, payload in events)
    assert not any(key in payload for _, payload in events for key in ("messages", "code", "response"))
    saved_messages = list(Path(result["evidenceDirectory"]).glob("*-messages.json"))
    assert len(saved_messages) == 1
    assert any(m.get("role") == "tool_result" for m in json.loads(saved_messages[0].read_text())["messages"])
    # A real earlier-format expected-literal authority did not store a mode.
    # Its persisted document, hash and source contract must still validate.
    evidence = json.loads(Path(result["evidenceRef"]).read_text())
    evidence["decision"].pop("correctionMode")
    raw = json.dumps(evidence).encode()
    Path(result["evidenceRef"]).write_bytes(raw)
    result.pop("correctionMode")
    result["evidenceDigest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert validate_adjudication_record(spec, probe.to_dict(), receipt, result) == result


def test_real_adapter_submits_fixture_correction_without_changing_acceptance_or_candidate(tmp_path, monkeypatch):
    objective = "A reservation reduces available stock when enabled; a disabled reservation leaves it unchanged."
    source = "def reserve(stock, amount, *, enabled):\n    return stock - amount if enabled else stock\n"
    wrong = ("from stock import reserve\ndef test_enabled_reservation():\n"
             "    remaining = reserve(9, 3, enabled=False)\n    assert remaining == 6\n")
    right = wrong.replace("enabled=False", "enabled=True")
    case = _case(tmp_path, monkeypatch, objective=objective, source=source, wrong=wrong,
        right=right, scenario="Enabled reservations reduce available stock by their quantity.")
    spec, probe, receipt, context, valid, commands = case
    valid["reason"] = "The scenario requires an enabled reservation, but the fixture disables it; the acceptance assertion stays unchanged."
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
               "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    context._runtime = runtime
    parent = ToolRegistry([read_file_tool, write_file_tool])
    adapter = OpenAIModelAdapter(runtime, parent)
    original = {p.name: p.read_bytes() for p in Path(context.cwd).iterdir()}
    requests = []

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        assert len(requests) <= 2
        schema = {tool["function"]["name"]: tool["function"] for tool in body["tools"]}
        assert set(schema) == {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
        assert "correctionMode" not in schema["completion_probe_adjudication_submit"]["parameters"]["properties"]
        value = deepcopy(valid)
        if len(requests) == 1:
            value["correctionMode"] = "fixture_inputs"  # The model cannot assign host authority.
        else:
            output = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "tool")
            assert "probe_adjudication_invalid" in output
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"fixture-{len(requests)}",
            "type": "function", "function": {"name": "completion_probe_adjudication_submit", "arguments": json.dumps(value)}}]}
        return Response(json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    result = _assess(case, adapter, tools=parent)
    assert result["verdict"] == "probe_error" and result["correctionMode"] == "fixture_inputs"
    assert result["replacement"]["code"] == right
    assert result["receiptRef"] == receipt["receiptRef"]
    assert validate_adjudication_record(spec, probe.to_dict(), receipt, result) == result
    assert {p.name: p.read_bytes() for p in Path(context.cwd).iterdir()} == original
    assert len(commands) == 1, "Independent attribution cannot execute or approve its replacement."
    assert adapter.tools is parent
    assert not any(value == "approved" for value in result.values())

    # Even matching persisted bytes cannot turn this newly admitted fixture mode
    # into a legacy expected-literal authority by dropping its host mode.
    evidence = json.loads(Path(result["evidenceRef"]).read_text())
    evidence["decision"].pop("correctionMode")
    raw = json.dumps(evidence).encode()
    Path(result["evidenceRef"]).write_bytes(raw)
    result.pop("correctionMode")
    result["evidenceDigest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="correction mode changed"):
        validate_adjudication_record(spec, probe.to_dict(), receipt, result)


def _direct_driver(monkeypatch, case, action):
    model, budget = object(), AgentTurnBudget()

    def loop(**values):
        assert values["model"] is model and values["agent_budget"] is budget
        assert values["implementation_spec"] is None and values["implementation_task"] is False
        assert values["max_steps"] is None and values["budget_exhausted_policy"] == "raise"
        assert values["cwd"] != case[3].cwd
        assert set(values["tools"].list_all()) == {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
        packet = json.loads(values["messages"][1]["content"])
        child = ToolContext(cwd=values["cwd"], permissions=values["permissions"])
        if packet.get("displayOnly"):
            request, parts = {"documentId": packet["documentRef"]}, []
            while True:
                result = values["tools"].execute("completion_evidence_read", request, child)
                assert result.ok
                page = json.loads(result.output)
                parts.append(page["text"])
                if page["nextCursor"] is None:
                    break
                request = {"cursor": page["nextCursor"]}
            packet = json.loads("".join(parts))
        assert packet["probe"] == case[1].to_dict() and packet["receipt"] == case[2]
        return action(values, child, budget)

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", loop)
    return model, budget


@pytest.mark.parametrize("kind", ["changed_call", "removed_assertion", "changed_scenario"])
def test_general_replacement_remains_pending_separate_audit_without_execution(tmp_path, monkeypatch, kind):
    from minicode.probe_supersession import read_audit_record

    case = _case(tmp_path, monkeypatch)
    spec, probe, receipt, context, proposal, commands = case
    original = {p.name: p.read_bytes() for p in Path(context.cwd).iterdir()}
    original_probe, original_receipt = probe.to_dict(), deepcopy(receipt)
    proposed = deepcopy(proposal)
    if kind == "changed_call":
        proposed["replacement"]["code"] = RIGHT.replace("release(8, 4)", "release(12, 0)")
    elif kind == "removed_assertion":
        proposed["replacement"]["code"] = "def test_release():\n    pass\n"
    else:
        proposed["replacement"]["scenario"] = "Different obligation"

    def action(values, child, _budget):
        result = values["tools"].execute("completion_probe_adjudication_submit", proposed, child)
        assert result.ok and result._implementation_submitted
        assert json.loads(result.output)["verdict"] == "probe_error"

    model, budget = _direct_driver(monkeypatch, case, action)
    result = _assess(case, model, budget=budget)
    assert result["verdict"] == "probe_error" and result["correctionMode"] == "semantic_replacement"
    assert result["replacement"] == proposed["replacement"]
    assert validate_adjudication_record(spec, original_probe, original_receipt, result) == result
    authority = json.loads(Path(result["authorityRef"]).read_text())
    assert authority["originalObjective"] == spec.objective
    assert authority["probe"] == original_probe and authority["receipt"] == original_receipt
    assert probe.to_dict() == original_probe and receipt == original_receipt
    assert len(commands) == 1, "Only the original failed pytest ran; attribution cannot execute its proposal."
    assert {p.name: p.read_bytes() for p in Path(context.cwd).iterdir()} == original
    assert "approved" not in result.values() and "product_gap" not in result.values()
    assert not {"supersessionRef", "coverageAudit", "retiredProbeIds"} & result.keys()
    # The accepted attribution is still a pending replacement proposal. Its
    # durable authority must never stand in for the separate coverage verdict.
    with pytest.raises(ValueError, match="coverage audit"):
        read_audit_record(result)


@pytest.mark.parametrize("kind", ["changed_criteria", "outside_source"])
def test_unbound_correction_cannot_become_a_semantic_authority(tmp_path, monkeypatch, kind):
    case = _case(tmp_path, monkeypatch)

    def action(values, child, _budget):
        value = deepcopy(case[4])
        if kind == "changed_criteria":
            value["replacement"]["criterionIds"] = ["unknown"]
        else:
            value["sourceEvidence"][0]["path"] = "../outside.py"
        result = values["tools"].execute("completion_probe_adjudication_submit", value, child)
        assert not result.ok and not result._implementation_submitted
        assert json.loads(result.output)["errorCode"] == "probe_adjudication_invalid"

    model, budget = _direct_driver(monkeypatch, case, action)
    result = _assess(case, model, budget=budget)
    assert result["verdict"] == "inconclusive" and "replacement" not in result


def test_true_product_gap_has_evidence_but_no_replacement_or_delivery_approval(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, product_gap=True)

    def action(values, child, _budget):
        result = values["tools"].execute("completion_probe_adjudication_submit", case[4], child)
        assert result.ok and result._implementation_submitted

    model, budget = _direct_driver(monkeypatch, case, action)
    result = _assess(case, model, budget=budget)
    assert result["verdict"] == "product_gap" and "replacement" not in result
    assert Path(case[3].cwd, "stock.py").read_text().endswith("return stock\n")


@pytest.mark.parametrize("tamper", ["objective", "candidate", "source", "receipt", "record", "evidence", "authority", "symlink"])
def test_restore_rejects_changed_inputs_or_host_evidence(tmp_path, monkeypatch, tamper):
    case = _case(tmp_path, monkeypatch)

    def action(values, child, _budget):
        assert values["tools"].execute("completion_probe_adjudication_submit", case[4], child).ok

    model, budget = _direct_driver(monkeypatch, case, action)
    result = _assess(case, model, budget=budget)
    spec, source, receipt, *_rest = case
    source, receipt, result = source.to_dict(), deepcopy(receipt), deepcopy(result)
    if tamper == "objective":
        spec = ImplementationSpec(spec.write_paths, spec.checks, OBJECTIVE + " Changed.")
    elif tamper == "candidate":
        receipt["candidateDigest"] = "sha256:" + "1" * 64
    elif tamper == "source":
        source["code"] += "# changed\n"
    elif tamper == "receipt":
        receipt["receipt"]["evidence"]["exitCode"] = 0
    elif tamper == "record":
        result["verdict"] = "product_gap"
    elif tamper in {"evidence", "authority"}:
        Path(result["evidenceRef" if tamper == "evidence" else "authorityRef"]).write_text("{}")
    else:
        link = tmp_path / "00001-decision.json"
        link.symlink_to(result["evidenceRef"])
        result["evidenceRef"] = str(link)
    with pytest.raises(ValueError):
        validate_adjudication_record(spec, source, receipt, result)


@pytest.mark.parametrize("exception", ["budget", "interrupt"])
def test_control_flow_error_survives_failed_final_evidence_write(tmp_path, monkeypatch, exception):
    case = _case(tmp_path, monkeypatch)
    owner = ProbeFailureAdjudicator(case[0])
    persist = owner._persist

    def broken(kind, document):
        if kind == "messages":
            raise OSError("disk unavailable")
        return persist(kind, document)

    monkeypatch.setattr(owner, "_persist", broken)

    def action(values, child, budget):
        if exception == "budget":
            raise AgentBudgetExceeded("local budget exhaustion", budget.snapshot())
        raise KeyboardInterrupt("preserve interrupt")

    model, budget = _direct_driver(monkeypatch, case, action)
    with pytest.raises(AgentBudgetExceeded if exception == "budget" else KeyboardInterrupt):
        _assess(case, model, budget=budget, owner=owner)
