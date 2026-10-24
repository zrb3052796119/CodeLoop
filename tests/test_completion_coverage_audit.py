"""Real isolated pytest receipts and native independent coverage authority."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.completion_coverage_audit import audit_completion_coverage
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units, _profile
from minicode.implementation_execution import ImplementationSpec, implementation_candidate_digest
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.scheduler_permissions import SchedulerWorkerReadPermissions
from minicode.tooling import ToolContext, ToolDefinition, ToolRegistry, ToolResult
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool


OBJECTIVE = "1. hold subtracts the requested amount.\n2. release adds the requested amount.\n"
PRODUCT = "def hold(n, amount):\n    return n - amount\n\ndef release(n, amount):\n    return n + amount\n"
PROBE = "from stock import hold, release\ndef test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 2) == 5\n"


def _case(tmp_path, monkeypatch, *, code=PROBE, product=PRODUCT):
    from minicode.workflow_workspace import WorkflowWorkspaceTransaction

    root = tmp_path / "candidate"
    root.mkdir()
    (root / "stock.py").write_text(product)
    (root / "test_public.py").write_text("def test_public():\n    assert True\n")
    check = VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20)
    spec = ImplementationSpec(("stock.py", "test_public.py"), (check,), OBJECTIVE)
    units = list(requirement_units(OBJECTIVE))
    probe = ReviewerProbe.create(criterion_ids=[u["id"] for u in units], scenario="Both quantity operations", code=code)
    commands = []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            commands.append((argv, cwd))
            return argv

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    verifier = DeterministicVerifier(sandbox_backend=Sandbox(), explicit_trusted_executables={"python3": sys.executable})
    with WorkflowWorkspaceTransaction(str(root)) as transaction:
        frozen = transaction.freeze(allowed_paths=spec.write_paths, paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen, check=check, verifier=verifier,
            test_path="test_public.py", read_roots=(".",), forbidden_paths=(".git", ".env"),
            run_kwargs={"workflow_id": "wf_" + "a" * 32, "task_id": "task_coverage", "execution_assignment_id": "asg_coverage",
                "patch_digest": frozen.patch_digest, "permissions": PermissionManager(str(root), prompt=None), "permission_cwd": str(root)})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
    assert receipt["status"] == "passed"
    budget = AgentTurnBudget()
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1", "openaiApiKey": "test",
               "modelMaxRetries": 0, "maxOutputTokens": 4096}
    host = ToolContext(cwd=str(root), _runtime=runtime, _agent_budget=budget)
    candidate_digest = implementation_candidate_digest(spec, host)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    saved = []

    def persist(kind, document):
        path = evidence_root / f"{len(saved):05d}-{kind}.json"
        path.write_text(json.dumps(document, sort_keys=True, ensure_ascii=False))
        saved.append((kind, path))
        return str(path)

    wrapped = {"probeId": probe.probe_id, "candidateDigest": candidate_digest, "receipt": receipt,
               "receiptRef": persist("pytest-receipt", receipt)}
    evidence = {"source": probe.to_dict(), "receipt": wrapped, "purpose": "acceptance", "active": True}

    def read(value, _context):
        assert value["probeId"] == probe.probe_id
        document = json.dumps({"source": probe.to_dict(), "receipts": [wrapped]}, sort_keys=True, ensure_ascii=False)
        offset, limit = value.get("offset", 0), value.get("limit", 8000)
        end = min(len(document), offset + limit)
        return ToolResult(ok=True, output=json.dumps({"text": document[offset:end], "nextOffset": end if end < len(document) else None,
                                                    "totalChars": len(document)}))

    read_probe = ToolDefinition(name="review_probe_read", description="Read exact retained current probe source and receipt",
        input_schema={"type": "object", "properties": {"probeId": {"type": "string"}, "receiptView": {"type": "string"},
            "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["probeId"]},
        validator=lambda value: value, run=read, capability_profile=_profile())
    tools = ToolRegistry([read_file_tool, write_file_tool, read_probe])
    adapter = OpenAIModelAdapter(runtime, tools)
    permissions = SchedulerWorkerReadPermissions(str(root), read_roots=(".",), forbidden_paths=(".git",))
    owner = ImplementationCompletionReview(spec)
    rows = [{"id": unit["id"], "status": "covered", "probeIds": [probe.probe_id], "rationale": "Both operations work.",
             "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}]} for unit in units]
    proposal = {"status": "approved", "requirements": rows, "findings": [], "patchDigest": candidate_digest}

    def assess(*, model=adapter, sink=None, checkpoint=lambda: None):
        return audit_completion_coverage(objective=OBJECTIVE, requirements=units, proposal=proposal,
            candidate_digest=candidate_digest, model=model, tools=tools, cwd=str(root), permissions=permissions,
            host=host, event_sink=sink, persist=persist, checkpoint=checkpoint,
            probe_evidence=lambda _pid: evidence,
            source_anchors=lambda anchors: owner._anchors(anchors, root, permissions))

    valid = {"status": "approved", "requirements": [{"id": u["id"], "status": "supported",
        "reason": "The actual hold/release entry receives a nonzero quantity and its returned stock is asserted.",
        "witnesses": [{"probeId": probe.probe_id, "startLine": 3, "endLine": 4}]} for u in units], "findings": []}
    return locals()


class _CoverageRequests(list):
    """Evidence-phase requests, with every real inspection request retained."""

    def __init__(self):
        super().__init__()
        self.inspection_requests = []
        self.all_requests = []


def _inspection_action(body):
    names = {item['function']['name'] for item in body['tools']}
    if 'completion_coverage_inspection_submit' not in names:
        return None
    assert 'completion_coverage_audit_submit' not in names
    inspection_users = [m for m in body['messages'] if m['role'] == 'user']
    assert len(inspection_users) == 1
    assert set(json.loads(inspection_users[0]['content'])) == {'coverageInspection'}
    packet = next(json.JSONDecoder().raw_decode(m['content'][m['content'].index('{"coverageInspection":'):])[0]['coverageInspection']
                  for m in body['messages'] if '"coverageInspection"' in m.get('content', ''))
    reads = [m for m in body['messages'] if m['role'] == 'tool' and 'FILE: stock.py\n' in m.get('content', '')]
    if not reads:
        return 'read_file', {'path': 'stock.py'}
    source = reads[-1]['content'].split('TRUNCATED:', 1)[1].split('\n', 1)[1]
    assert source.strip()
    return 'completion_coverage_inspection_submit', {
        'status': 'ready_for_evidence', 'observations': [{
            'requirementId': packet['requirements'][0]['id'], 'status': 'uncertain',
            'sourceEvidence': [{'path': 'stock.py', 'startLine': 1, 'endLine': min(5, len(source.splitlines()))}],
            'expectedBehavior': packet['requirements'][0]['text'],
            'observedBehavior': 'Read the actual stock.py source; execution and full coverage remain to be reconciled.',
            'probeIds': []}], 'findings': []}


def _coverage_packet(body):
    return next(json.loads(m['content']) for m in body['messages']
                if m['role'] == 'user' and '"evidenceIndex"' in m.get('content', ''))


def _inspection_step(messages, registry):
    """Internal scripted models still execute both native inspection tools."""
    from minicode.types import AgentStep
    body = {'tools': [{'function': {'name': tool.name}} for tool in registry.list()],
            'messages': [{**m, 'role': 'tool' if m['role'] == 'tool_result' else m['role']}
                         for m in messages]}
    action = _inspection_action(body)
    if action is None:
        return None
    name, value = action
    return AgentStep(type='tool_calls', calls=[{'id': f'inspection-{len(messages)}',
        'toolName': name, 'input': value}])


def _native(monkeypatch, case, actions):
    requests = _CoverageRequests()

    class Response(io.BytesIO):
        status = 200

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.all_requests.append(body)
        action = _inspection_action(body)
        if action is not None:
            requests.inspection_requests.append(body)
            identity = f'inspection-{len(requests.inspection_requests)}'
        else:
            requests.append(body)
            identity = f'audit-{len(requests)}'
            assert len(requests) <= len(actions)
            action = actions[len(requests) - 1]
            if callable(action):
                action = action(body)
        name, value = action
        message = ({"role": "assistant", "content": value} if name is None else
            {"role": "assistant", "content": None, "tool_calls": [{"id": identity,
                "type": "function", "function": {"name": name, "arguments": json.dumps(value)}}]})
        return Response(json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": "stop" if name is None else "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    return requests


def _read(case, *, offset=0, limit=16000):
    return "review_probe_read", {"probeId": case["probe"].probe_id, "receiptView": "current", "offset": offset, "limit": limit}


def test_native_document_cursors_deliver_complete_source_across_provider_turns(tmp_path, monkeypatch):
    code = PROBE + ("# retained generated test context: nonzero stock quantities\n" * 350)
    case = _case(tmp_path, monkeypatch, code=code)
    observed = {}

    def continue_reading(body):
        packet = next(json.loads(m["content"]) for m in body["messages"]
                      if m["role"] == "user" and '"evidenceIndex"' in m.get("content", ""))
        item = packet["evidenceIndex"][0]
        required = [item["sourceDocumentId"], item["currentReceiptDocumentId"]]
        pages, latest = {}, {}
        for message in body["messages"]:
            if message["role"] != "tool":
                continue
            if message['content'].startswith('FILE: '):
                continue  # Actual independent candidate-source read.
            value = json.JSONDecoder().raw_decode(message["content"])[0]
            if "documentId" not in value:
                continue
            identity = value["documentId"]
            assert value["candidateDigest"] == case["candidate_digest"]
            pages.setdefault(identity, {})[value["offset"]] = value["text"]
            latest[identity] = value
        for identity in required:
            page = latest.get(identity)
            for dependency in page["requiredDocumentIds"] if page else ():
                if dependency not in required:
                    required.append(dependency)
            if page is None:
                return "review_evidence_read", {"documentId": identity}
            if page["nextCursor"] is not None:
                return "review_evidence_read", {"cursor": page["nextCursor"]}
            assert page["completeDelivered"]
        source = json.loads("".join(text for _, text in sorted(pages[item["sourceDocumentId"]].items())))
        assert source == case["probe"].to_dict()
        assert len(pages[item["sourceDocumentId"]]) > 1
        observed.update(source=source, pages=len(pages[item["sourceDocumentId"]]))
        return "completion_coverage_audit_submit", case["valid"]

    requests = _native(monkeypatch, case, [
        ("completion_coverage_audit_submit", case["valid"]), *([continue_reading] * 12)])
    result = case["assess"]()
    assert result["status"] == "approved"
    assert observed["source"]["code"] == code
    assert len(requests.inspection_requests) == 2
    assert case["budget"].snapshot().used_model_calls == len(requests.all_requests)
    # The actual provider messages contain only host cursors for continuation;
    # the model never has to reconstruct an offset or repeat a receipt view.
    inputs = [json.loads(call["function"]["arguments"])
              for message in requests[-1]["messages"] for call in message.get("tool_calls", [])
              if call["function"]["name"] == "review_evidence_read"]
    assert any(set(value) == {"cursor"} for value in inputs)
    assert all(set(value) in ({"documentId"}, {"cursor"}) for value in inputs)


def test_native_audit_requires_actual_source_read_then_binds_current_assertions(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    original = {p.name: p.read_bytes() for p in case["root"].iterdir()}
    initial_schema = deepcopy(case["adapter"].request_tool_schemas())
    requests = _native(monkeypatch, case, [("completion_coverage_audit_submit", case["valid"]), _read(case),
                                         ("completion_coverage_audit_submit", case["valid"])])
    events = []

    class Sink:
        def emit(self, name, *, step=None, payload=None):
            events.append((name, payload))

    result = case["assess"](sink=Sink())
    assert result["status"] == "approved" and len(requests) == 3
    assert "read the exact complete probe source" in next(m["content"] for m in reversed(requests[1]["messages"]) if m["role"] == "tool")
    assert all({t["function"]["name"] for t in request["tools"]} == {
        "read_file", "review_probe_read", "review_evidence_read", "completion_coverage_audit_submit", "completion_evidence_read"}
        for request in requests)
    assert case["adapter"].request_tool_schemas() == initial_schema and case["adapter"].tools is case["tools"]
    assert len(requests.inspection_requests) == 2
    assert case["budget"].snapshot().used_model_calls == len(requests.all_requests) == 5
    assert all('"evidenceIndex"' not in json.dumps(body['messages']) for body in requests.inspection_requests)
    assert any(m['role'] == 'tool' and '"grantsCoverage": false' in m.get('content', '')
               for m in requests[0]['messages'])
    assert result["judgmentKind"] == "independent_model_semantic_coverage"
    witness = result["requirements"][0]["witnesses"][0]
    assert witness["sourceDigest"] == case["probe"].source_digest
    assert witness["receiptRef"] == case["wrapped"]["receiptRef"]
    assert witness["lineDigest"] == "sha256:" + hashlib.sha256("".join(PROBE.splitlines(True)[2:4]).encode()).hexdigest()
    authority = json.loads(Path(result["authorityRef"]).read_text())
    assert authority["originalObjective"] == OBJECTIVE and authority["proposal"] == case["proposal"]
    assert authority["probeEvidence"][case["probe"].probe_id] == case["evidence"]
    assert {p.name: p.read_bytes() for p in case["root"].iterdir()} == original
    saved_messages = [json.loads(p.read_text())["messages"] for kind, p in case["saved"] if kind == "coverage-audit-messages"]
    assert len(saved_messages) >= 5 and any(m.get("role") == "tool_result" for m in saved_messages[-1])
    assert events and all(name == "implementation.completion_review_activity" for name, _ in events)
    assert all(not {"code", "messages", "response"} & row.keys() for _, row in events)


def test_full_authority_is_saved_without_duplicating_receipt_history_in_initial_context(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    case["proposal"].update(probeReceipts=[{"output": "old execution " * 2000}],
                            diagnosticObservations=[{"output": "old diagnostic " * 2000}])
    requests = _native(monkeypatch, case, [(None, "Everything is approved.")])
    result = case["assess"]()
    assert result["status"] == "inconclusive"
    packet = _coverage_packet(requests[0])
    assert set(packet["proposal"]) == {"status", "requirements", "findings"}
    assert result["reason"] == "coverage_audit_interrupted" and result["auditPending"]
    # An interrupted attempt has no decision receipt. Its actual request still
    # binds the immutable authority saved before the native audit started.
    authority_path = Path(packet["authorityRef"])
    assert authority_path in [path for kind, path in case["saved"] if kind == "coverage-audit-authority"]
    authority = json.loads(authority_path.read_text())
    assert authority["proposal"] == case["proposal"]
    assert packet["proposalDigest"] == authority["proposalDigest"]
    assert "old execution" not in json.dumps(requests[0]["messages"])


def test_partial_source_page_cannot_be_used_as_full_read_authority(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    source = json.dumps(case["probe"].to_dict(), sort_keys=True, ensure_ascii=False)
    document = json.dumps({"source": case["probe"].to_dict(), "receipts": [case["wrapped"]]},
                          sort_keys=True, ensure_ascii=False)
    split = document.index(source) + len(source) // 2
    requests = _native(monkeypatch, case, [_read(case, limit=split),
        ("completion_coverage_audit_submit", case["valid"]), _read(case, offset=split),
        ("completion_coverage_audit_submit", case["valid"])])
    result = case["assess"]()
    assert result["status"] == "approved" and len(requests) == 4
    assert "read the exact complete probe source" in next(m["content"] for m in reversed(requests[2]["messages"]) if m["role"] == "tool")


@pytest.mark.parametrize("tamper", ["candidate", "retired", "diagnostic", "criterion", "setup_only", "out_of_range", "missing_id", "findings"])
def test_invalid_positive_evidence_cannot_approve(tmp_path, monkeypatch, tamper):
    case = _case(tmp_path, monkeypatch)
    value = deepcopy(case["valid"])
    if tamper == "candidate":
        case["wrapped"]["candidateDigest"] = "sha256:" + "0" * 64
    elif tamper == "retired":
        case["evidence"]["active"] = False
    elif tamper == "diagnostic":
        case["evidence"]["purpose"] = "diagnostic"
    elif tamper == "criterion":
        value["requirements"][0]["witnesses"][0]["probeId"] = "unrelated-probe"
    elif tamper == "setup_only":
        value["requirements"][0]["witnesses"][0].update(startLine=1, endLine=1)
    elif tamper == "out_of_range":
        value["requirements"][0]["witnesses"][0]["endLine"] = 999
    elif tamper == "missing_id":
        value["requirements"].pop()
    else:
        value["findings"] = ["The release contract is unverified."]
    negative = {"status": "inconclusive", "requirements": [], "findings": ["Current evidence is not sufficient."]}
    requests = _native(monkeypatch, case, [_read(case), ("completion_coverage_audit_submit", value),
                                         ("completion_coverage_audit_submit", negative)])
    result = case["assess"]()
    assert result["status"] == "inconclusive" and len(requests) == 3
    assert "coverage_audit_invalid" in next(m["content"] for m in reversed(requests[-1]["messages"]) if m["role"] == "tool")


WEAK = [
    "from stock import hold\ndef test_inventory():\n    assert hold(5, 2) == 3\n",
    "from stock import hold, release\ndef test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 0) == 3\n",
    "from stock import hold\ndef release(n, amount):\n    return n + amount\ndef test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 2) == 5\n",
]


@pytest.mark.parametrize("code", WEAK, ids=["borrowed_criterion", "zero_input", "substitute"])
def test_independent_authority_can_reject_passing_self_claim_without_certifying_other_rows(tmp_path, monkeypatch, code):
    case = _case(tmp_path, monkeypatch, code=code, product=PRODUCT.replace("n + amount", "n"))
    verdict = {"status": "changes_required", "requirements": [{"id": case["units"][1]["id"],
        "status": "missing", "reason": "The actual release entry still returns its input for nonzero amount; this passing probe does not establish addition.",
        "witnesses": []}], "findings": ["release does not add the requested amount."]}
    requests = _native(monkeypatch, case, [_read(case), ("completion_coverage_audit_submit", verdict)])
    result = case["assess"]()
    assert case["proposal"]["status"] == "approved" and case["receipt"]["status"] == "passed"
    assert result["status"] == "changes_required" and len(result["requirements"]) == 1
    assert len(requests) == 2 and len(case["commands"]) == 1
    assert "return n\n" in (case["root"] / "stock.py").read_text()


def test_interruption_preserves_actual_child_messages_and_full_tool_evidence(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)

    def interrupt(_body):
        raise KeyboardInterrupt()

    _native(monkeypatch, case, [_read(case), interrupt])
    with pytest.raises(KeyboardInterrupt):
        case["assess"]()
    assert not any(kind == "coverage-audit-decision" for kind, _ in case["saved"])
    latest = [p for kind, p in case["saved"] if kind == "coverage-audit-messages"][-1]
    assert any(m.get("role") == "tool_result" for m in json.loads(latest.read_text())["messages"])
    assert any(kind == "coverage-audit-tool-evidence" for kind, _ in case["saved"])


def test_omitted_original_requirement_is_rejected_before_any_model_request(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    case["units"].pop()
    requests = _native(monkeypatch, case, [])
    with pytest.raises(ValueError, match="complete original requirement partition"):
        case["assess"]()
    assert requests == []
