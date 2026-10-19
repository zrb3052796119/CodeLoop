"""Real isolated probes and unforgeable completion coverage on generic tasks."""

import json
from pathlib import Path
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import ImplementationCompletionReview, requirement_units
from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
from minicode.permissions import PermissionDeniedError, PermissionManager
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.read_file import read_file_tool
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken
from minicode.types import AgentStep
from test_completion_coverage_audit import _inspection_step


OBJECTIVE = "# Inventory\n\n1. hold subtracts the requested amount.\n2. release adds the requested amount.\n"
SOURCE = "def hold(n, amount):\n    return n - amount\n\ndef release(n, amount):\n    return n + amount\n"
PROBE = "from stock import hold, release\ndef test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 2) == 5\n"


class _DeliveredRegistry:
    """Scripted child drivers must append receipts before acknowledging delivery."""

    def __init__(self, registry, messages):
        self.registry = registry
        self.messages = messages

    def __getattr__(self, name):
        return getattr(self.registry, name)

    def execute(self, name, value, context):
        result = self.registry.execute(name, value, context)
        identity = "fixture-delivery-" + str(len(self.messages))
        self.messages.extend([
            {"role": "assistant_tool_call", "toolUseId": identity, "toolName": name, "input": value},
            {"role": "tool_result", "toolUseId": identity, "toolName": name,
             "content": result.output, "isError": not result.ok},
        ])
        if result._delivery_ack is not None:
            result._delivery_ack()
        return result


def _fixture(tmp_path, monkeypatch, *, permissions=None, review_factory=ImplementationCompletionReview,
             adjudication_driver=None, coverage_driver=None):
    import minicode.workflow_runner as runner

    (tmp_path / "stock.py").write_text(SOURCE)
    (tmp_path / "test_public.py").write_text("def test_legacy():\n    assert True\n")
    commands = []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **kwargs):
            commands.append({"argv": list(argv), "cwd": str(cwd)})
            return argv

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *a, **k: DeterministicVerifier(sandbox_backend=Sandbox(),
                            explicit_trusted_executables={"python3": sys.executable}))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *a, **k: None)
    spec = ImplementationSpec(("stock.py", "test_public.py"), (VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),), OBJECTIVE)
    context = ToolContext(cwd=str(tmp_path), permissions=permissions)
    capability = direct_verification_capability(spec)
    review = review_factory(spec)
    model = object()
    tools = ToolRegistry([read_file_tool, edit_file_tool])

    def assess(driver, **kwargs):
        import minicode.agent_loop as loop

        def review_loop(**values):
            assert values["model"] is model
            assert values["implementation_spec"] is None
            assert values["max_steps"] is None
            assert values["implementation_task"] is False
            assert values["budget_exhausted_policy"] == "raise"
            assert not {"task", "edit_file", "write_file", "run_command"} & set(values["tools"].list_all())
            assert values["cwd"] != str(tmp_path)
            child = ToolContext(cwd=values["cwd"], permissions=values["permissions"])
            if "completion_coverage_inspection_submit" in values["tools"].list_all():
                values["tools"] = _DeliveredRegistry(values["tools"], values["context_manager"].messages)
                while True:
                    step = _inspection_step(values["context_manager"].messages, values["tools"])
                    assert step is not None and len(step.calls) == 1
                    call = step.calls[0]
                    result = values["tools"].execute(call["toolName"], call["input"], child)
                    assert result.ok, result.output
                    if call["toolName"] == "completion_coverage_inspection_submit":
                        assert json.loads(result.output)["grantsCoverage"] is False
                        break
                return AgentStep(type="assistant", kind="final", content="Actual candidate read; coverage remains to be audited.")
            packet = (next(json.loads(message["content"]) for message in values["messages"]
                           if message.get("role") == "user" and '"evidenceIndex"' in message.get("content", ""))
                      if "completion_coverage_audit_submit" in values["tools"].list_all()
                      else json.loads(values["messages"][1]["content"]))
            if "completion_coverage_audit_submit" in values["tools"].list_all():
                values["tools"] = _DeliveredRegistry(values["tools"], values["context_manager"].messages)
                if coverage_driver is not None:
                    return coverage_driver(values["tools"], child, packet)
                while True:
                    step = _native_coverage_step(values["context_manager"].messages)
                    assert step is not None and len(step.calls) == 1
                    call = step.calls[0]
                    result = values["tools"].execute(call["toolName"], call["input"], child)
                    assert result.ok, result.output
                    if call["toolName"] == "completion_coverage_audit_submit":
                        break
                return AgentStep(type="assistant", kind="final", content="Independent scripted inventory coverage accepted.")
            if "completion_probe_adjudication_submit" in values["tools"].list_all():
                # Older focused tests script only this new semantic model
                # boundary; actual probe execution and evidence remain real.
                if adjudication_driver is not None:
                    return adjudication_driver(values["tools"], child, packet)
                result = values["tools"].execute("completion_probe_adjudication_submit", {
                    "verdict": "product_gap", "reason": "The original inventory contract remains an acceptance obligation.",
                    "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
                    "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}]}, child)
                assert result.ok, result.output
                return AgentStep(type="assistant", kind="final", content="Independent product gap recorded")
            packet.update(json.loads(values["messages"][0]["content"].split(
                "=== ORIGINAL REQUIREMENT AUTHORITY ===\n", 1)[1]))
            assert packet["originalObjective"] == OBJECTIVE
            child = ToolContext(cwd=values["cwd"], permissions=values["permissions"])
            return driver(values["tools"], child, packet)

        monkeypatch.setattr(loop, "run_agent_turn", review_loop)
        checks = capability.ensure_completion_checks(context)
        assert checks["status"] == "passed"
        return review.assess(model=model, tools=tools, context=context, agent_budget=None,
            event_sink=None, cancellation_token=None, deadline_monotonic=None,
            checks_receipt=checks, **kwargs)

    return review, assess, commands, capability


def _coverage_approval(packet, sources):
    """Script the separate semantic boundary for this known inventory fixture.

    These protocol tests do not claim to measure a model's semantic judgment.
    Dedicated weak-evidence tests supply their own rejecting coverage_driver.
    """
    approved = packet["proposal"]["status"] == "approved"
    value = {"status": "approved" if approved else "changes_required",
        "findings": [] if approved else ["The actual inventory behavior still needs the reported implementation repair."],
        "requirements": [
        {"id": row["id"], "status": "supported" if row["status"] == "covered" else "missing",
         "reason": "Independent inventory assertions support this fixture contract." if row["status"] == "covered"
            else "The current inventory consumer does not yet satisfy the original operation.",
         "witnesses": [{"probeId": pid, "startLine": 1,
                        "endLine": len(sources[pid]["code"].splitlines())} for pid in row["probeIds"]]}
        for row in packet["proposal"]["requirements"]]}
    if packet.get("supersessionIndex"):
        value["supersessions"] = [{"id": edge["id"], "status": "accepted",
            "invalidityReason": "The independent inventory fixture records an error in the generated test.",
            "requirements": [{"id": criterion,
                "reason": "The replacement still exercises the actual inventory operation and its original quantity assertion.",
                "witnesses": [{"probeId": edge.get("effectiveProbeId", edge["newProbeId"]), "startLine": 1,
                    "endLine": len(sources[edge.get("effectiveProbeId", edge["newProbeId"])]["code"].splitlines())}]}
                for criterion in edge["criterionIds"]]} for edge in packet["supersessionIndex"]]
    return value


def _coverage_probe_ids(packet):
    return list(dict.fromkeys([*[row["probeId"] for row in packet["evidenceIndex"]],
        *[edge[key] for edge in packet.get("supersessionIndex", [])
          for key in ("oldProbeId", "newProbeId", "effectiveProbeId") if key in edge]]))


def _native_coverage_step(messages, *, stable_evidence=False, registry=None):
    """Execute inspection when requested, then the unchanged native evidence audit."""
    if registry is not None:
        inspection_step = _inspection_step(messages, registry)
        if inspection_step is not None:
            return inspection_step
    packets = [json.loads(m["content"]) for m in messages
               if m.get("role") == "user" and '"evidenceIndex"' in m.get("content", "")]
    if not packets:
        return None
    packet = packets[-1]
    if stable_evidence:
        return _native_stable_coverage_step(messages, packet)
    results = [m for m in messages if m.get("role") == "tool_result"
               and m.get("toolName") == "review_probe_read"]
    inputs = {m["toolUseId"]: m["input"] for m in messages
              if m.get("role") == "assistant_tool_call" and m.get("toolName") == "review_probe_read"}
    pages, ends = {}, {}
    for result in results:
        assert not result["isError"], result["content"]
        request = inputs[result["toolUseId"]]
        page = json.JSONDecoder().raw_decode(result["content"])[0]
        pid = request["probeId"]
        pages.setdefault(pid, {})[request.get("offset", 0)] = page["text"]
        ends[pid] = page["nextOffset"]
    sources = {}
    for pid in _coverage_probe_ids(packet):
        if pid not in pages or ends[pid] is not None:
            value = {"probeId": pid, "receiptView": "current", "limit": 16000,
                     "offset": ends.get(pid, 0)}
            return AgentStep(type="tool_calls", calls=[{"id": "audit-read-" + str(len(results)),
                "toolName": "review_probe_read", "input": value}])
        sources[pid] = json.loads("".join(text for _, text in sorted(pages[pid].items())))["source"]
    obligation_read = _native_obligation_step(messages, packet)
    if obligation_read is not None:
        return obligation_read
    name, value = "completion_coverage_audit_submit", _coverage_approval(packet, sources)
    return AgentStep(type="tool_calls", calls=[{"id": "audit-" + str(len(results)), "toolName": name, "input": value}])


def _native_obligation_step(messages, packet):
    """Display projections cannot stand in for complete supersession authority."""
    results = [message for message in messages if message.get("role") == "tool_result"
               and message.get("toolName") == "review_evidence_read"]
    last, dependencies = {}, {}
    for result in results:
        assert not result["isError"], result["content"]
        page = json.JSONDecoder().raw_decode(result["content"])[0]
        last[page["documentId"]] = page
        dependencies[page["documentId"]] = page["requiredDocumentIds"]
    required = list(dict.fromkeys(edge["obligationDocumentId"] for edge in packet.get("supersessionIndex", [])))
    for identity in required:
        for dependency in dependencies.get(identity, []):
            if dependency not in required:
                required.append(dependency)
        page = last.get(identity)
        if page is None or page["nextCursor"] is not None:
            value = {"documentId": identity} if page is None else {"cursor": page["nextCursor"]}
            return AgentStep(type="tool_calls", calls=[{"id": "audit-obligation-" + str(len(results)),
                "toolName": "review_evidence_read", "input": value}])
        assert page["completeDelivered"], "A final page alone must not certify a document with gaps."
    return None


def _native_stable_coverage_step(messages, packet):
    """Consume host document cursors, including independent authority dependencies."""
    results = [m for m in messages if m.get("role") == "tool_result"
               and m.get("toolName") == "review_evidence_read"]
    pages, last, dependencies = {}, {}, {}
    for result in results:
        assert not result["isError"], result["content"]
        page = json.JSONDecoder().raw_decode(result["content"])[0]
        identity = page["documentId"]
        pages.setdefault(identity, {})[page["offset"]] = page["text"]
        last[identity] = page
        dependencies[identity] = page["requiredDocumentIds"]
    required = list(dict.fromkeys([
        *[row[key] for row in packet["evidenceIndex"]
          for key in ("sourceDocumentId", "currentReceiptDocumentId") if row.get(key)],
        *[edge["obligationDocumentId"] for edge in packet.get("supersessionIndex", [])]]))
    for identity in required:
        for dependency in dependencies.get(identity, []):
            if dependency not in required:
                required.append(dependency)
        page = last.get(identity)
        if page is None or page["nextCursor"] is not None:
            value = {"documentId": identity} if page is None else {"cursor": page["nextCursor"]}
            return AgentStep(type="tool_calls", calls=[{"id": "audit-document-" + str(len(results)),
                "toolName": "review_evidence_read", "input": value}])
        assert page["completeDelivered"], "A final page alone must not certify a document with gaps."
    sources = {}
    supersession_sources = []
    for identity in required:
        kind = last[identity]["documentType"]
        if kind not in {"probe_source", "supersession_review_material"}:
            continue
        document = json.loads("".join(text for _, text in sorted(pages[identity].items())))
        if kind == "probe_source":
            supersession_sources.append(document)
        else:
            assert document["reviewMaterialVersion"] == 1
            supersession_sources.extend(document[key] for key in ("oldSource", "newSource", "effectiveSource"))
    for probe_id in _coverage_probe_ids(packet):
        candidates = [row for row in packet["evidenceIndex"] if row["probeId"] == probe_id]
        if candidates:
            identity = candidates[0]["sourceDocumentId"]
            sources[probe_id] = json.loads("".join(text for _, text in sorted(pages[identity].items())))
        else:
            # Both the old dependency closure and the new self-contained host
            # material carry exact sources. Only use fully delivered documents.
            sources[probe_id] = next(source for source in supersession_sources
                                    if source.get("probeId") == probe_id)
    return AgentStep(type="tool_calls", calls=[{"id": "audit-" + str(len(results)),
        "toolName": "completion_coverage_audit_submit", "input": _coverage_approval(packet, sources)}])


def _rows(packet, *, ids=(), status="covered"):
    return [{"id": unit["id"], "status": status,
             "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
             "probeIds": list(ids), "rationale": "The real inventory consumer exercises both operations."}
            for unit in packet["requirements"]]


def _probe(tools, child, packet, code=PROBE, **extra):
    result = tools.execute("review_test", {"purpose": "acceptance", "criterionIds": [r["id"] for r in packet["requirements"]],
        "scenario": "Both inventory operations preserve quantity semantics", "code": code, **extra}, child)
    return result, json.loads(result.output) if result.output.startswith("{") else None


def _submit(tools, child, packet, *, ids=(), rows=None, status="approved", findings=None):
    return tools.execute("completion_review_submit", {"status": status,
        "requirements": rows if rows is not None else _rows(packet, ids=ids),
        "findings": findings or []}, child)


def test_requirement_identity_covers_late_duplicates_and_fenced_contract_without_truncation():
    objective = "# Title\n\n" + "Long background. " * 400 + "\n\n1. Same requirement.\n2. Same requirement.\n\n```python\ncontract = 3\n```\n\nFinal mandatory consumer.\n"
    units = requirement_units(objective)
    assert units == requirement_units(objective)
    assert len({u["id"] for u in units}) == len(units)
    assert units[-1]["text"] == "Final mandatory consumer."
    for number, line in enumerate(objective.splitlines(), 1):
        if not line.strip():
            continue
        owners = [u for u in units if u["startLine"] <= number <= u["endLine"]]
        assert len(owners) == 1
        assert line in owners[0]["text"]


def test_literal_path_inventory_groups_without_weakening_headings_or_behavior_bullets():
    objective = ("# Implement the listed consumers\n"
        "- `src/alpha.py`\n"
        "- `tests/test_alpha.py::test_write[one]`\n"
        "- `test_beta.py::test_read`\n"
        "The consumers must share the new validation rule.\n\n"
        "- `src/gamma.py`\n"
        "- `src/delta.py`\n\n"
        "# Preserve API behavior\n"
        "- `hold()` must reject invalid amounts.\n"
        "- `release()` must accept valid amounts.\n"
        "- `lookup()`\n"
        "- `save()`\n")
    units = requirement_units(objective)
    assert units == requirement_units(objective)
    assert units[0]["text"] == "# Implement the listed consumers"
    assert units[1]["text"] == "\n".join(objective.splitlines()[1:4])
    assert units[2]["text"] == "The consumers must share the new validation rule."
    assert units[3]["text"] == "\n".join(objective.splitlines()[6:8])
    assert units[4]["text"] == "# Preserve API behavior\n- `hold()` must reject invalid amounts."
    assert units[5]["text"] == "- `release()` must accept valid amounts."
    assert units[6]["text"] == "- `lookup()`"
    assert units[7]["text"] == "- `save()`"
    assert {unit["kind"] for unit in units} == {"behavior"}
    for number, line in enumerate(objective.splitlines(), 1):
        if line.strip():
            owners = [u for u in units if u["startLine"] <= number <= u["endLine"]]
            assert len(owners) == 1 and line in owners[0]["text"]


def test_real_independent_probe_binds_source_and_current_candidate_without_mutating_it(tmp_path, monkeypatch):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)
    original = {name: (tmp_path / name).read_bytes() for name in ("stock.py", "test_public.py")}

    def driver(tools, child, packet):
        result, evidence = _probe(tools, child, packet)
        assert result.ok, result.output
        assert evidence["receipt"]["testReport"]["passed"] == 1
        assert evidence["candidateDigest"] == packet["candidateDigest"]
        submitted = _submit(tools, child, packet, ids=[evidence["probeId"]])
        assert submitted.ok and submitted._implementation_submitted, submitted.output

    result = assess(driver)
    assert result["status"] == "approved"
    assert result["patchDigest"] != result["frozenPatchDigest"]
    assert len(commands) == 2  # one public check, one independent probe
    assert all(Path(row["cwd"]) != tmp_path for row in commands)
    assert original == {name: (tmp_path / name).read_bytes() for name in original}
    assert Path(result["evidenceRef"]).is_file()
    assert review.state["probes"][0]["code"] == PROBE
    assert review.state["receipts"][0]["receipt"]["patchDigest"] == result["frozenPatchDigest"]
    capability.close()


@pytest.mark.parametrize("attempt", ["no_probe", "duplicate_id", "missing_id", "forged_probe", "bad_line", "outside_source"])
def test_green_public_checks_and_declarations_cannot_forge_requirement_coverage(tmp_path, monkeypatch, attempt):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        rows = _rows(packet)
        if attempt != "no_probe":
            result, evidence = _probe(tools, child, packet)
            assert result.ok
            rows = _rows(packet, ids=[evidence["probeId"]])
        if attempt == "duplicate_id":
            rows[1]["id"] = rows[0]["id"]
        elif attempt == "missing_id":
            rows.pop()
        elif attempt == "forged_probe":
            rows[0]["probeIds"] = ["review_probe_" + "0" * 24]
        elif attempt == "bad_line":
            rows[0]["sourceEvidence"][0]["endLine"] = 500
        elif attempt == "outside_source":
            rows[0]["sourceEvidence"][0]["path"] = "../stock.py"
        rejected = _submit(tools, child, packet, rows=rows)
        assert not rejected.ok
        if attempt == "missing_id":
            diagnostic = json.loads(rejected.output)
            assert diagnostic["errorCode"] == "completion_review_invalid"
            assert "each original requirement ID" in diagnostic["message"]

    assert assess(driver)["status"] == "inconclusive"
    capability.close()


def test_unchanged_candidate_reuses_decision_but_edits_replay_original_probe(tmp_path, monkeypatch):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)

    def first(tools, child, packet):
        result, evidence = _probe(tools, child, packet)
        assert result.ok
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    approved = assess(first)
    count = len(commands)
    assert assess(lambda *a: pytest.fail("same candidate must not ask another model")) == approved
    assert len(commands) == count
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def repaired_review(tools, child, packet):
        prior = packet["retainedProbes"]
        assert prior[0]["status"] == "failed"
        # A second happy probe cannot erase the retained failing obligation.
        result, other = _probe(tools, child, packet, "from stock import hold\ndef test_hold():\n    assert hold(5, 2) == 3\n")
        assert result.ok
        assert not _submit(tools, child, packet, ids=[other["probeId"]]).ok
        rows = _rows(packet, status="missing")
        assert _submit(tools, child, packet, rows=rows, status="changes_required",
                       findings=["release still leaves the inventory unchanged."]).ok

    result = assess(repaired_review)
    assert result["status"] == "changes_required"
    assert result["repairProbes"] == [review.state["probes"][0]]
    assert result["repairProbes"][0]["code"] == PROBE
    assert {row["probeId"] for row in result["repairProbes"]} == {
        row["probeId"] for row in result["probeReceipts"] if row["receipt"]["status"] == "failed"}
    assert (tmp_path / "stock.py").read_text() == SOURCE.replace("return n + amount", "return n")
    assert len(commands) == count + 3  # current public check, retained replay, new independent probe
    assert len(review.state["probes"]) == 2
    capability.close()


def test_inconclusive_review_delivers_complete_probe_source_to_workspace_scoped_executor(tmp_path, monkeypatch):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    broken = "import unavailable_fixture_package\n" + PROBE

    def driver(tools, child, packet):
        result, evidence = _probe(tools, child, packet, broken)
        assert not result.ok and evidence["receipt"]["status"] == "inconclusive"
        # The reviewer can also exit without a valid native submission; that
        # must retain actionable evidence, not just an inaccessible temp path.

    result = assess(driver)
    assert result["status"] == "inconclusive"
    assert result["repairProbes"] == []
    unresolved = result["unresolvedProbes"][0]
    assert unresolved["source"]["code"] == broken
    assert unresolved["source"]["criterionIds"]
    assert unresolved["source"]["scenario"]
    assert unresolved["receiptRef"] == result["probeReceipts"][0]["receiptRef"]
    assert unresolved["disposition"] == "unattributed_test_evidence_not_a_product_repair_instruction"
    assert result["probeReceipts"][0]["receipt"]["testReport"]["errors"] > 0
    assert (tmp_path / "stock.py").read_text() == SOURCE
    assert (tmp_path / "test_public.py").read_text() == "def test_legacy():\n    assert True\n"
    capability.close()


def test_probes_and_readonly_review_cannot_read_parent_denied_source(tmp_path, monkeypatch):
    denied = tmp_path / "secret.txt"
    denied.write_text("PRIVATE_UNAVAILABLE")
    (tmp_path / "alias.txt").symlink_to("secret.txt")

    class Permissions:
        def ensure_path_access(self, target, intent):
            if Path(target).resolve() == denied:
                raise PermissionDeniedError("denied", code="path_assignment_scope_denied")

        def ensure_command(self, *args, **kwargs):
            pass

    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch, permissions=Permissions())

    def driver(tools, child, packet):
        assert not (Path(child.cwd) / "secret.txt").exists()
        assert not (Path(child.cwd) / "alias.txt").exists()
        code = PROBE + "\nfrom pathlib import Path\ndef test_scope():\n    assert not Path('secret.txt').exists()\n    assert not Path('alias.txt').exists()\n"
        result, evidence = _probe(tools, child, packet, code)
        assert result.ok, result.output
        rows = _rows(packet, ids=[evidence["probeId"]])
        rows[0]["sourceEvidence"][0]["path"] = "secret.txt"
        assert not _submit(tools, child, packet, rows=rows).ok

    assert assess(driver)["status"] == "inconclusive"
    assert denied.read_text() == "PRIVATE_UNAVAILABLE"
    capability.close()


def test_cancel_during_review_is_not_an_approval_or_swallowed_failure(tmp_path, monkeypatch):
    review, _assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    context = ToolContext(cwd=str(tmp_path))
    receipt = capability.ensure_completion_checks(context)
    token = TurnCancellationToken("turn_" + "1" * 32)
    token.request()
    with pytest.raises(TurnCancellationRequested):
        review.assess(model=object(), tools=ToolRegistry([]), context=context, agent_budget=None,
            event_sink=None, cancellation_token=token, deadline_monotonic=None, checks_receipt=receipt)
    assert not review.state["reviews"]
    capability.close()


def test_missing_requirement_can_be_reported_without_invented_source_or_probe(tmp_path, monkeypatch):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        rows = _rows(packet, status="unknown")
        for row in rows:
            row["sourceEvidence"] = []
        result = _submit(tools, child, packet, rows=rows, status="inconclusive",
                         findings=["Consumer path evidence has not yet been established."])
        assert result.ok, result.output

    assert assess(driver)["status"] == "inconclusive"
    # A transient/unclassified review is retryable on unchanged bytes.
    assert assess(driver)["status"] == "inconclusive"
    capability.close()


def test_explicit_fixture_correction_keeps_original_failure_and_assertions(tmp_path, monkeypatch):
    review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    broken = "from stock import hold, release\ndef test_inventory():\n    amount = int('invalid fixture')\n    assert hold(5, amount) == 3\n    assert release(3, amount) == 5\n"
    corrected = broken.replace("int('invalid fixture')", "int('2')")

    def driver(tools, child, packet):
        failed, prior = _probe(tools, child, packet, broken)
        assert not failed.ok and prior["receipt"]["status"] == "failed"
        source = [{"path": "stock.py", "startLine": 1, "endLine": 5}]
        rejected, _ = _probe(tools, child, packet, corrected.replace("== 3", "== 999"),
            correctsProbeId=prior["probeId"], correctionReason="Wrong expectation is not a fixture correction.",
            sourceEvidence=source)
        assert not rejected.ok
        fixed, replacement = _probe(tools, child, packet, corrected,
            correctsProbeId=prior["probeId"], correctionReason="Quantity inputs are integers in the public source.",
            sourceEvidence=source)
        assert fixed.ok, fixed.output
        assert _submit(tools, child, packet, ids=[replacement["probeId"]]).ok

    assert assess(driver)["status"] == "approved"
    assert len(review.state["probes"]) == 2
    assert len(review.state["retiredProbeIds"]) == 1
    assert len(review.state["corrections"]) == 1
    assert {r["receipt"]["status"] for r in review.state["receipts"]} == {"passed", "failed"}
    for restored in (ImplementationCompletionReview.from_state(review.spec, review.state),
                     ImplementationCompletionReview.from_archive(review.spec, review.state["evidenceDirectory"])):
        for key in ("probes", "receipts", "corrections", "retiredProbeIds"):
            assert restored.state[key] == review.state[key]
    capability.close()


def test_reviewer_events_cannot_end_or_assign_outcome_to_parent(tmp_path, monkeypatch):
    review, _assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    import minicode.agent_loop as loop
    events = []

    class Sink:
        def emit(self, event_type, **fields):
            events.append((event_type, fields))

    def nested(**kwargs):
        assert kwargs["context_manager"].messages == kwargs["messages"]
        sink = kwargs["event_sink"]
        for kind in ("task.outcome", "task.completed", "skill.routed", "run.completed"):
            sink.emit(kind, payload={"outcome": "success"})
        sink.emit("model.completed", step=1, payload={"operationId": "modelop_" + "a" * 32,
            "resultType": "assistant", "toolCallCount": 0, "content": "PRIVATE_PROSE"})

    monkeypatch.setattr(loop, "run_agent_turn", nested)
    context = ToolContext(cwd=str(tmp_path))
    result = review.assess(model=object(), tools=ToolRegistry([]), context=context, agent_budget=None,
        event_sink=Sink(), cancellation_token=None, deadline_monotonic=None,
        checks_receipt=capability.ensure_completion_checks(context))
    assert result["status"] == "inconclusive"
    assert len(events) == 1
    assert events[0][0] == "implementation.completion_review_activity"
    assert events[0][1]["payload"]["role"] == "completion_reviewer"
    assert "PRIVATE_PROSE" not in json.dumps(events)
    assert Path(result["evidenceDirectory"]).is_dir()
    capability.close()


def test_actual_nested_loop_stops_on_native_submission_without_replacing_executor(tmp_path, monkeypatch):
    review, _assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    all_requests, request_tools = [], []

    class SameConfiguredModel:
        def next(self, messages, **kwargs):
            all_requests.append(messages)
            request_tools.append(set(kwargs["tools_override"].list_all()))
            audit_step = _native_coverage_step(messages, registry=kwargs["tools_override"])
            if audit_step is not None:
                return audit_step
            authority = next(message["content"].split("=== ORIGINAL REQUIREMENT AUTHORITY ===\n", 1)[1]
                             for message in messages if "=== ORIGINAL REQUIREMENT AUTHORITY ===\n" in message.get("content", ""))
            packet = json.loads(authority)
            if len(all_requests) == 1:
                name, values = "review_test", {"purpose": "acceptance", "criterionIds": [r["id"] for r in packet["requirements"]],
                    "scenario": "Both public inventory operations work", "code": PROBE}
            else:
                assert len(all_requests) == 2, "accepted native submission must not spend an extra model turn"
                evidence = json.loads(next(message["content"] for message in reversed(messages)
                    if message.get("role") == "tool_result" and message.get("toolName") == "review_test"))
                name, values = "completion_review_submit", {"status": "approved", "findings": [],
                    "requirements": _rows(packet, ids=[evidence["probeId"]])}
            return AgentStep(type="tool_calls", calls=[{"id": "review-" + str(len(all_requests)),
                "toolName": name, "input": values}])

    context = ToolContext(cwd=str(tmp_path), _runtime={"model": "default"})
    result = review.assess(model=SameConfiguredModel(), tools=ToolRegistry([read_file_tool, edit_file_tool]),
        context=context, agent_budget=None, event_sink=None, cancellation_token=None,
        deadline_monotonic=None, checks_receipt=capability.ensure_completion_checks(context))
    assert result["status"] == "approved"
    assert len(all_requests) == 6  # proposal, actual inspection and evidence audit terminate natively
    assert ["completion_coverage_inspection_submit" in names for names in request_tools] == [False, False, True, True, False, False]
    assert any(message.get("toolName") == "read_file" and message.get("role") == "tool_result"
               and "return n + amount" in message.get("content", "") for message in all_requests[3])
    assert (tmp_path / "stock.py").read_text() == SOURCE
    capability.close()


def test_collection_error_can_be_explicitly_corrected_without_discarding_evidence(tmp_path, monkeypatch):
    review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    broken = "import nonexistent_unused_fixture_package\n" + PROBE

    def driver(tools, child, packet):
        failed, prior = _probe(tools, child, packet, broken)
        assert not failed.ok and prior["receipt"]["status"] == "inconclusive"
        assert prior["receipt"]["testReport"]["errors"] > 0
        fixed, replacement = _probe(tools, child, packet, PROBE,
            correctsProbeId=prior["probeId"], correctionReason="The unrelated fixture dependency is unused by the public consumer.",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert fixed.ok, fixed.output
        assert _submit(tools, child, packet, ids=[replacement["probeId"]]).ok

    assert assess(driver)["status"] == "approved"
    assert len(review.state["receipts"]) == 2
    assert review.state["corrections"][0]["correctionKind"] == "pytest_setup_error"
    restored = ImplementationCompletionReview.from_state(review.spec, review.state)
    assert restored.state["corrections"] == review.state["corrections"]
    assert restored.state["receipts"] == review.state["receipts"]
    capability.close()


def test_current_unavailable_execution_cannot_authorize_fixture_retirement(tmp_path, monkeypatch):
    import minicode.implementation_completion_review as completion

    review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    executions = []

    def unavailable(**kwargs):
        executions.append(kwargs["probe"].probe_id)
        return {"probeId": kwargs["probe"].probe_id, "patchDigest": kwargs["frozen"].patch_digest,
                "status": "inconclusive", "reason": "review_probe_execution_unavailable",
                "evidence": {"failureReason": "timeout"}, "testReport": None}

    monkeypatch.setattr(completion, "run_isolated_review_probe", unavailable)

    def driver(tools, child, packet):
        failed, prior = _probe(tools, child, packet)
        assert not failed.ok and prior["receipt"]["status"] == "inconclusive"
        rejected, _ = _probe(tools, child, packet, "\n" + PROBE,
            correctsProbeId=prior["probeId"], correctionReason="Timeout alone cannot justify a fixture correction.",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert not rejected.ok

    assert assess(driver)["status"] == "inconclusive"
    assert len(executions) == 1
    assert review.state["retiredProbeIds"] == []
    assert review.state["corrections"] == []
    capability.close()


def test_previously_passed_probe_remains_an_obligation_after_current_fixture_failure(tmp_path, monkeypatch):
    review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    probe = ("from stock import hold, release\ndef test_inventory():\n"
             "    amount = int(hold(4, 2))\n"
             "    assert hold(5, amount) == 3\n    assert release(3, amount) == 5\n")
    retained = []

    def first(tools, child, packet):
        result, receipt = _probe(tools, child, packet, probe)
        assert result.ok
        retained.append(receipt["probeId"])
        assert _submit(tools, child, packet, ids=retained).ok

    assert assess(first)["status"] == "approved"
    (tmp_path / "stock.py").write_text(SOURCE.replace("return n - amount", "return 'invalid quantity'"))

    def second(tools, child, packet):
        assert packet["retainedProbes"][0]["status"] == "failed"
        rejected, _ = _probe(tools, child, packet, "\n" + probe,
            correctsProbeId=retained[0], correctionReason="A real earlier pass cannot be retired by relabeling a regression.",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert not rejected.ok

    assert assess(second)["status"] == "inconclusive"
    assert [row["receipt"]["status"] for row in review.state["receipts"]] == ["passed", "failed"]
    assert review.state["retiredProbeIds"] == []
    capability.close()
