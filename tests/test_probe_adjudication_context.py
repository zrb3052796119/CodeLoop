"""Native pytest/host review and actual OpenAI wire keep history advisory.

Only the provider response boundary is scripted. No real model or benchmark
fixture is used; the stock example deliberately contains an unrelated gap.
"""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.completion_evidence_view import EvidenceViews
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import implementation_candidate_digest
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.permissions import PermissionManager
from minicode.probe_adjudication_context import build_advisory_context, project_advisory_context
from minicode.probe_failure_adjudication import ProbeFailureAdjudicator, validate_adjudication_record
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.reviewer_verification import ReviewerProbe, ReviewerProbeLedger
from minicode.tooling import ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from test_probe_failure_adjudication import _case


OBJECTIVE = "1. release adds the amount to stock.\n2. hold subtracts the amount from stock."
SOURCE = "def release(stock, amount):\n    return stock + amount\n\ndef hold(stock, amount):\n    return stock\n"
NEW_PROBE = "from stock import hold\ndef test_hold():\n    assert hold(8, 4) == 4\n"


def _response(name=None, arguments=None, *, identity="native", text=None):
    message = {"role": "assistant", "content": text}
    if name is not None:
        message["tool_calls"] = [{"id": identity, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]

    class Response(io.BytesIO):
        status = 200

    return Response(json.dumps({"choices": [{"index": 0, "message": message,
        "finish_reason": "tool_calls" if name else "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())


def _packet(body):
    return next(json.loads(m["content"]) for m in body["messages"]
        if m.get("role") == "user" and (m.get("content") or "").startswith("{")
        and "originalObjective" in json.loads(m["content"]))


def _assess(case, probe, receipt, *, advisory_context=None):
    return ProbeFailureAdjudicator(case["spec"]).assess(
        probe=probe.to_dict(), receipt=receipt, context=case["context"],
        model=case["model"], tools=case["tools"], agent_budget=None,
        event_sink=None, cancellation_token=None, deadline_monotonic=None,
        **({"advisory_context": advisory_context} if advisory_context is not None else {}))


@pytest.fixture
def history_case(tmp_path, monkeypatch):
    spec, old, old_receipt, context, value, _commands = _case(
        tmp_path, monkeypatch, objective=OBJECTIVE, source=SOURCE)
    runtime = {"model": "gpt-test", "openaiBaseUrl": "https://openai.invalid/v1",
        "openaiApiKey": "test", "modelMaxRetries": 0, "maxOutputTokens": 4096}
    context._runtime = runtime
    tools = ToolRegistry([read_file_tool, write_file_tool])
    case = {"spec": spec, "old": old, "old_receipt": old_receipt, "context": context,
            "tools": tools, "model": OpenAIModelAdapter(runtime, tools)}
    old_requests = []

    def prior_transport(request, *, timeout):
        body = json.loads(request.data)
        old_requests.append(body)
        assert len(old_requests) == 1
        return _response("completion_probe_adjudication_submit", value)

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", prior_transport)
    prior = _assess(case, old, old_receipt)
    assert prior["verdict"] == "probe_error"

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **_kwargs):
            return argv

    verifier = DeterministicVerifier(sandbox_backend=Sandbox(),
        explicit_trusted_executables={"python3": sys.executable})
    probe = ReviewerProbe.create(criterion_ids=list(old.criterion_ids),
        scenario="The requested hold operation subtracts from stock.", code=NEW_PROBE)
    with WorkflowWorkspaceTransaction(context.cwd) as transaction:
        frozen = transaction.freeze(allowed_paths=spec.write_paths, paths_relative_to_cwd=True)
        receipt = run_isolated_review_probe(probe=probe, frozen=frozen, check=spec.checks[0],
            verifier=verifier, test_path="test_public.py", read_roots=(".",), forbidden_paths=(".git", ".env"),
            run_kwargs={"workflow_id": "wf_" + "b" * 32, "task_id": "task_history",
                "execution_assignment_id": "asg_history", "patch_digest": frozen.patch_digest,
                "permissions": PermissionManager(context.cwd, prompt=None), "permission_cwd": context.cwd})
        receipt = ReviewerProbeLedger().record(probe, frozen.patch_digest, receipt)
    assert receipt["status"] == "failed" and receipt["testReport"]["failed"] == 1
    source_ref = tmp_path / "current-source.json"
    source_ref.write_text(json.dumps(probe.to_dict()))
    wrapped = {"probeId": probe.probe_id, "candidateDigest": old_receipt["candidateDigest"],
        "sourceRef": str(source_ref), "receipt": receipt}
    receipt_ref = tmp_path / "current-receipt.json"
    receipt_ref.write_text(json.dumps(wrapped))
    wrapped["receiptRef"] = str(receipt_ref)
    case.update(prior=prior, probe=probe, receipt=wrapped, verifier=verifier,
        probes={old.probe_id: old, probe.probe_id: probe})
    return case


def _context(case, *, records=None, chains=()):
    return build_advisory_context(spec=case["spec"], probe=case["probe"].to_dict(),
        receipt=case["receipt"], probes=case["probes"],
        adjudications=[case["prior"]] if records is None else records, chains=chains)


def test_native_host_wire_reads_exact_history_then_independently_attributes_new_probe(history_case, monkeypatch):
    case = history_case
    review = ImplementationCompletionReview(case["spec"])
    for probe, receipt in ((case["old"], case["old_receipt"]), (case["probe"], case["receipt"])):
        review._register_purpose(probe, "acceptance")
        review._receipts[(receipt["candidateDigest"], probe.probe_id)] = deepcopy(receipt)
        review._attempts.append(deepcopy(receipt))
    prior = {**case["prior"], "adjudicationRef": review._persist("probe-adjudication", case["prior"])}
    review._failure_adjudications.append(prior)
    # This verifies the real public target; the native review's public-check
    # input is its intentionally small status/digest contract.
    assert implementation_candidate_digest(case["spec"], case["context"]) == case["receipt"]["candidateDigest"], "before public"
    public = subprocess.run([sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", "test_public.py"],
        cwd=case["context"].cwd, capture_output=True, check=False,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
    assert public.returncode == 0
    assert implementation_candidate_digest(case["spec"], case["context"]) == case["receipt"]["candidateDigest"], "after public"
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
        lambda *_args, **_kwargs: case["verifier"])
    before = {p.name: p.read_bytes() for p in Path(case["context"].cwd).iterdir() if p.is_file()}
    old_authority = json.loads(Path(prior["authorityRef"]).read_text())
    originals = {"authority": old_authority, "source": old_authority["probe"],
        "receipt": old_authority["receipt"], "decision": json.loads(Path(prior["evidenceRef"]).read_text())}
    requests, completed = [], {}
    pending, active, parts = [], None, []

    def transport(request, *, timeout):
        nonlocal active, parts
        body = json.loads(request.data)
        requests.append(body)
        assert len(requests) < 35
        names = {tool["function"]["name"] for tool in body["tools"]}
        assert names == {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
        packet = _packet(body)
        assert packet["probe"] == case["probe"].to_dict()
        history = packet.get("advisoryContext")
        assert history is not None, "The host must supply related history to the actual new adjudicator wire."
        assert history["semantics"] == "advisory_only_not_authority_or_permission"
        assert history["currentReceiptRef"] == case["receipt"]["receiptRef"]
        assert len(history["entries"]) == 1
        entry = history["entries"][0]
        assert entry["verdictClaim"] == "probe_error" and entry["probeId"] == case["old"].probe_id
        assert "code" not in json.dumps(history), "History is an index, not another full source/receipt tree."
        if len(requests) == 1:
            pending.extend((key, {"documentId": entry[key]["documentRef"]}) for key in originals)
        if active is not None:
            message = next(m for m in body["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == active[1])
            page = json.JSONDecoder().raw_decode(message["content"])[0]
            assert page["displayOnly"] is True and "completeDelivered" not in page
            parts.append(page["text"])
            if page.get("nextCursor"):
                pending.insert(0, (active[0], {"cursor": page["nextCursor"]}))
            else:
                completed[active[0]] = json.loads("".join(parts))
                assert completed[active[0]] == originals[active[0]]
                parts = []
            active = None
        if pending:
            key, arguments = pending.pop(0)
            identity = f"read-original-{len(requests)}"
            active = (key, identity)
            return _response("completion_evidence_read", arguments, identity=identity)
        assert completed == originals
        return _response("completion_probe_adjudication_submit", {
            "verdict": "product_gap", "reason": "This different current probe exercises hold, which returns unchanged stock.",
            "objectiveEvidence": [{"startLine": 2, "endLine": 2}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 5}]}, identity="new-judgment")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    result = review.assess(model=case["model"], tools=case["tools"], context=case["context"],
        agent_budget=None, event_sink=None, cancellation_token=None, deadline_monotonic=None,
        checks_receipt={"status": "passed", "patchDigest": case["receipt"]["candidateDigest"]})
    assert requests and _packet(requests[0]).get("advisoryContext") is not None, \
        f"The actual new adjudicator wire never received its host advisory index: {result.get('status')} {result.get('findings')}"
    assert result["status"] == "changes_required" and len(requests) > 4
    assert review._failure_adjudications[0] == prior
    current = review._failure_adjudications[-1]
    assert current["oldProbeId"] == case["probe"].probe_id and current["verdict"] == "product_gap"
    assert current["receiptRef"] == case["receipt"]["receiptRef"]
    assert current["evidenceRef"] != prior["evidenceRef"]
    validate_adjudication_record(case["spec"], case["probe"].to_dict(), case["receipt"],
        {k: v for k, v in current.items() if k != "adjudicationRef"})
    saved = json.loads(Path(current["authorityRef"]).read_text())["advisoryContext"]
    assert "probe" not in saved["entries"][0] and "receipt" not in saved["entries"][0]
    assert len(json.dumps(saved)) < 2500
    assert case["old"].probe_id not in review._retired, "Advisory evidence cannot accept the old replacement."
    assert review._supersessions and not review._corrections
    assert {p.name: p.read_bytes() for p in Path(case["context"].cwd).iterdir() if p.is_file()} == before


@pytest.mark.parametrize("changed", ["candidateDigest", "objectiveDigest"])
def test_foreign_history_is_excluded_before_any_historical_file_read(history_case, monkeypatch, changed):
    case = history_case
    foreign = {**case["prior"], changed: "sha256:" + "f" * 64,
        "authorityRef": "/unavailable/foreign-authority.json"}
    context = _context(case, records=[foreign])
    assert context["entries"] == [] and context["relatedRecordCount"] == 0
    view = project_advisory_context(context, spec=case["spec"], probe=case["probe"].to_dict(),
        receipt=case["receipt"], views=EvidenceViews(candidate_digest=case["receipt"]["candidateDigest"]))
    assert view["entries"] == [] and foreign[changed] not in json.dumps(view)
    requests = []

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        assert _packet(body)["advisoryContext"]["entries"] == []
        assert foreign[changed] not in request.data.decode()
        assert foreign["authorityRef"] not in request.data.decode()
        return _response(text="No independent verdict is submitted.")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    result = _assess(case, case["probe"], case["receipt"], advisory_context=context)
    assert len(requests) == 1 and result["verdict"] == "inconclusive"


def test_no_new_native_submission_cannot_inherit_old_probe_error(history_case, monkeypatch):
    case = history_case
    requests = []

    def transport(request, *, timeout):
        body = json.loads(request.data)
        requests.append(body)
        assert _packet(body)["advisoryContext"]["entries"][0]["verdictClaim"] == "probe_error"
        return _response(text="The earlier failure was attributed already.")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    result = _assess(case, case["probe"], case["receipt"], advisory_context=_context(case))
    assert len(requests) == 1
    assert result["verdict"] == "inconclusive" and "replacement" not in result
    assert result["oldProbeId"] == case["probe"].probe_id
    assert result["receiptRef"] == case["receipt"]["receiptRef"]


@pytest.mark.parametrize("changed", ["candidateDigest", "objectiveDigest", "currentProbeId", "currentReceiptRef"])
def test_advisory_packet_is_bound_to_the_current_assessment(history_case, changed):
    case = history_case
    context = _context(case)
    context[changed] = "different"
    with pytest.raises(ValueError, match="current failed probe"):
        project_advisory_context(context, spec=case["spec"], probe=case["probe"].to_dict(),
            receipt=case["receipt"], views=EvidenceViews(candidate_digest=case["receipt"]["candidateDigest"]))


def test_modified_history_reference_is_not_exposed_as_an_original(history_case):
    case = history_case
    context = _context(case)
    context["entries"][0]["decisionDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="advisory decision changed"):
        project_advisory_context(context, spec=case["spec"], probe=case["probe"].to_dict(),
            receipt=case["receipt"], views=EvidenceViews(candidate_digest=case["receipt"]["candidateDigest"]))


def test_native_full_history_index_keeps_record_omitted_from_preview(history_case, monkeypatch):
    case = history_case
    valid = {"verdict": "inconclusive", "reason": "This earlier independent judgment remained uncertain.",
        "objectiveEvidence": [], "sourceEvidence": []}
    monkeypatch.setattr("minicode.openai_adapter.open_verified_url",
        lambda _request, **_kwargs: _response("completion_probe_adjudication_submit", valid))
    # Distinct native authority/decision files, not fabricated evidence rows.
    records = [case["prior"], *[_assess(case, case["old"], case["old_receipt"]) for _ in range(4)]]
    context = _context(case, records=records)
    assert len(context["entries"]) == 5
    assert context["entries"][-1]["decisionRef"] == case["prior"]["evidenceRef"]
    requests, index_pages, original_pages = [], [], []
    stage = "index"

    def transport(request, *, timeout):
        nonlocal stage
        body = json.loads(request.data)
        requests.append(body)
        assert len(requests) < 20
        history = _packet(body)["advisoryContext"]
        assert len(history["entries"]) == 4 and history["relatedRecordCount"] == 5
        assert all(row["verdictClaim"] == "inconclusive" for row in history["entries"])
        if len(requests) == 1:
            return _response("completion_evidence_read", {"documentId": history["completeIndex"]["documentRef"]}, identity="full-index-1")
        page = json.JSONDecoder().raw_decode(next(m["content"] for m in reversed(body["messages"]) if m.get("role") == "tool"))[0]
        assert page["displayOnly"] is True
        (index_pages if stage == "index" else original_pages).append(page["text"])
        if page["nextCursor"]:
            return _response("completion_evidence_read", {"cursor": page["nextCursor"]}, identity=f"history-page-{len(requests)}")
        if stage == "index":
            complete = json.loads("".join(index_pages))["entries"]
            assert len(complete) == 5 and complete[-1]["verdictClaim"] == "probe_error"
            stage = "original"
            return _response("completion_evidence_read", {"documentId": complete[-1]["decision"]["documentRef"]}, identity="omitted-original")
        assert json.loads("".join(original_pages)) == json.loads(Path(case["prior"]["evidenceRef"]).read_text())
        return _response(text="History was read, but no new native decision is submitted.")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", transport)
    result = _assess(case, case["probe"], case["receipt"], advisory_context=context)
    assert len(requests) >= 3 and result["verdict"] == "inconclusive"
    assert index_pages and original_pages
