"""Explicit fixture correction is distinct from erasing a real failed check.

Unrelated Store/render examples run actual pytest in disposable candidate copies.
These are local mechanism tests, not a paid-model result or semantic-equivalence
proof for arbitrary input changes.
"""

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import sys

import pytest

from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.review_probe_correction import correction_artifact_id
from minicode.review_probe_execution import run_isolated_review_probe
from minicode.review_probe_source import review_probe_source_artifact_id
from minicode.reviewer_verification import ReviewerCheckCapability, ReviewerProbeLedger
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.workflow_workspace import WorkflowWorkspaceTransaction
from test_reviewer_result_submission import _bound


SOURCE = """class Store:
    def __init__(self):
        self._records = {}
    def put(self, key, value):
        self._records[key] = value
    def get(self, key):
        return self._records.get(key)
    def keys(self):
        return list(self._records)

def render(store):
    return sorted(store.keys())
"""
OLD = """from candidate import Store, render

def test_prepared_records_are_rendered():
    store = Store()
    store.put('kept', 7)
    assert store.get('kept') == 7
    assert store.keys() == ['kept']
    result = render(Store())
    assert result == ['kept']
"""
FIXED = OLD.replace("render(Store())", "render(store)")


def _input(code=OLD):
    return {"criterionIds": ["ac_behavior"], "scenario": "Render the records prepared in the supplied store.", "code": code}


def _correct(old, code=FIXED):
    return {**_input(code), "correctsProbeId": old["probeId"],
            "correctionReason": "The fixture prepared store but passed a different new empty Store; reuse the prepared instance without changing assertions.",
            "sourceEvidence": [{"path": "candidate.py", "startLine": 1, "endLine": 12}]}


@pytest.fixture
def store_case(tmp_path):
    def create(*, broken=False):
        workspace = tmp_path / ("broken" if broken else "correct")
        workspace.mkdir()
        (workspace / "candidate.py").write_text(SOURCE.replace("return sorted(store.keys())", "return []  # baseline"))
        (workspace / "test_public.py").write_text("from candidate import Store, render\ndef test_public(): assert render(Store()) == []\n")
        transaction = WorkflowWorkspaceTransaction(str(workspace))
        candidate_source = SOURCE.replace("return sorted(store.keys())", "return []  # real defect") if broken else SOURCE
        (transaction.cwd / "candidate.py").write_text(candidate_source)
        frozen = transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)
        assignment, store, channel, _submit, _options = _bound(build=False)
        ledger = ReviewerProbeLedger()
        authority = store._scheduler_authority
        case = SimpleNamespace(transaction=transaction, frozen=frozen, assignment=assignment,
            store=store, ledger=ledger, attempts=[], publications=[], candidate_source=candidate_source,
            original=(workspace / "candidate.py").read_bytes())

        class LocalFixtureSandbox:
            def wrap_command(self, *, argv, **_kwargs):
                return argv

        verifier = DeterministicVerifier(sandbox_backend=LocalFixtureSandbox(),
            explicit_trusted_executables={"python": sys.executable})
        check = VerificationCheck("public_store", "tests", ("python", "-m", "pytest", "-q", "test_public.py"), 15)

        def run(probe, _context):
            result = run_isolated_review_probe(probe=probe, frozen=case.frozen, check=check,
                test_path="test_public.py", verifier=verifier, read_roots=(".",), forbidden_paths=(".git", ".env"),
                run_kwargs={"workflow_id": assignment.workflow_id, "task_id": assignment.task_id,
                    "execution_assignment_id": assignment.assignment_id, "patch_digest": case.frozen.patch_digest})
            source_ref = review_probe_source_artifact_id(probe.probe_id)
            if store.read(source_ref) is None:
                store.publish(source_ref, probe.to_dict(), authority=authority)
            result.update(sourceRef=source_ref, receiptRef=f"artifact_store_receipt_{len(case.attempts)}")
            store.publish(result["receiptRef"], result, authority=authority)
            case.attempts.append(result)
            return result

        def publish(request, old, prior, new, _context):
            anchors = []
            for anchor in request.source_evidence:
                path = transaction.cwd / anchor["path"]
                data = path.read_bytes()
                assert 1 <= anchor["startLine"] <= anchor["endLine"] <= len(data.splitlines())
                anchors.append({**anchor, "sourceDigest": "sha256:" + hashlib.sha256(data).hexdigest()})
            document = dict(correctionVersion=1, resultType="ReviewProbeCorrection.v1",
                workflowId=assignment.workflow_id, taskId=assignment.task_id,
                assignmentId=assignment.assignment_id, patchDigest=case.frozen.patch_digest,
                oldProbeId=old.probe_id, newProbeId=request.replacement.probe_id,
                oldSourceRef=review_probe_source_artifact_id(old.probe_id), newSourceRef=new["sourceRef"],
                oldReceiptRefs=[receipt["receiptRef"] for receipt in prior], newReceiptRef=new["receiptRef"],
                sourceEvidence=anchors, reason=request.reason)
            result = {"correctionRef": correction_artifact_id(document), "document": document}
            store.publish(result["correctionRef"], document, authority=authority)
            case.publications.append(result)
            return result

        cap = ReviewerCheckCapability(assignment=assignment, channel=channel, ledger=ledger,
            patch_digest=frozen.patch_digest, run_probe=run, record_correction=publish)
        case.cap, case.run = cap, run
        case.registry, case.context = ToolRegistry([cap.tool]), ToolContext(_subagent_mailbox=channel)
        return case
    cases = []
    def factory(**kwargs):
        case = create(**kwargs)
        cases.append(case)
        return case
    yield factory
    for case in cases:
        case.cap.close()
        case.transaction.close()


def _failed_original(case):
    result = case.registry.execute("review_test", _input(), case.context)
    assert not result.ok
    old = json.loads(result.output)
    assert old["status"] == "failed" and old["evidence"]["exitCode"] == 1
    assert old["testReport"]["caseReport"]["cases"][0]["failureKind"] == "assertion"
    return old


def test_assertion_from_wrong_store_can_be_explicitly_corrected_with_same_assertions(store_case):
    case = store_case()
    old = _failed_original(case)
    result = case.registry.execute("review_test", _correct(old), case.context)
    assert result.ok, result.output
    corrected = json.loads(result.output)
    assert corrected["status"] == "passed" and corrected["correctedProbeId"] == old["probeId"]
    assert [r["evidence"]["exitCode"] for r in case.attempts] == [1, 0]
    assert {r["patchDigest"] for r in case.attempts} == {case.frozen.patch_digest}
    assert case.attempts[0]["evidence"]["argv"] == case.attempts[1]["evidence"]["argv"]
    assert json.loads(case.store.read(corrected["correctionRef"]).content) == case.publications[0]["document"]
    assert case.store.read(old["sourceRef"]) is not None and case.store.read(old["receiptRef"]) is not None
    feedback = json.loads(case.ledger.feedback())
    assert feedback["retiredProbeIds"] == [old["probeId"]]
    assert feedback["activeProbeIds"] == [corrected["probeId"]]
    assert len(feedback["attempts"]) == len(feedback["probes"]) == 2
    assert len(case.ledger.require_all_current_passed(case.frozen.patch_digest)) == 1
    assert (case.transaction.cwd / "candidate.py").read_text() == case.candidate_source


def test_new_passing_probe_alone_does_not_discard_failed_original(store_case):
    case = store_case()
    old = _failed_original(case)
    new = case.registry.execute("review_test", _input(FIXED), case.context)
    assert new.ok
    assert len(case.ledger.probes()) == 2 and not case.publications
    assert old["probeId"] in json.loads(case.ledger.feedback())["activeProbeIds"]
    with pytest.raises(ValueError, match="retained"):
        case.ledger.require_all_current_passed(case.frozen.patch_digest)


def test_corrected_fixture_cannot_erase_a_real_product_defect(store_case):
    case = store_case(broken=True)
    old = _failed_original(case)
    result = case.registry.execute("review_test", _correct(old), case.context)
    assert result.output.startswith("{"), result.output
    replacement = json.loads(result.output)
    assert not result.ok and replacement["status"] == "failed"
    assert replacement["correctedProbeId"] == old["probeId"]
    assert replacement["testReport"]["caseReport"]["cases"][0]["failureKind"] == "assertion"
    with pytest.raises(ValueError, match="retained"):
        case.ledger.require_all_current_passed(case.frozen.patch_digest)
    old_digest = case.frozen.patch_digest
    case.transaction.resume_for_repair(case.frozen)
    edited = ToolRegistry([edit_file_tool]).execute("edit_file", {
        "path": "candidate.py", "old": "return []  # real defect", "new": "return sorted(store.keys())"},
        ToolContext(cwd=str(case.transaction.cwd)))
    assert edited.ok and edited._file_changed, edited.output
    case.frozen = case.transaction.freeze(allowed_paths=("candidate.py",), paths_relative_to_cwd=True)
    assert case.frozen.patch_digest != old_digest
    case.ledger.replay(patch_digest=case.frozen.patch_digest, context=case.context, run_probe=case.run)
    assert [r["evidence"]["exitCode"] for r in case.attempts] == [1, 1, 0]
    assert [r["probeId"] for r in case.attempts] == [old["probeId"], replacement["probeId"], replacement["probeId"]]
    assert len(case.ledger.require_all_current_passed(case.frozen.patch_digest)) == 1


def test_changed_assertion_is_specific_policy_rejection_not_tool_crash(store_case):
    case = store_case()
    old = _failed_original(case)
    result = case.registry.execute("review_test", _correct(old, FIXED.replace("result == ['kept']", "result == []")), case.context)
    assert not result.ok and "error[review_probe_correction_rejected]" in result.output
    assert "assertion predicates" in result.output and "tool_crashed" not in result.output
    assert len(case.attempts) == 1 and not case.publications
    assert old["probeId"] in json.loads(case.ledger.feedback())["activeProbeIds"]


@pytest.mark.parametrize("failure", ["execution", "publication", "wrong-owner"])
def test_runtime_or_storage_failure_is_not_misreported_as_policy_rejection(store_case, failure):
    case = store_case()
    old = _failed_original(case)
    def unavailable(*_args):
        raise ValueError("synthetic runtime storage failure")
    if failure == "execution":
        case.cap._run_probe = unavailable
    elif failure == "publication":
        case.cap._record_correction = unavailable
    context = replace(case.context, _subagent_mailbox=object()) if failure == "wrong-owner" else case.context
    result = case.registry.execute("review_test", _correct(old), context)
    assert not result.ok and "review_probe_correction_rejected" not in result.output
    assert old["probeId"] in json.loads(case.ledger.feedback())["activeProbeIds"]
    assert not case.publications
