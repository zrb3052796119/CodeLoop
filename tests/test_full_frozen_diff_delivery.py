"""Complete frozen patches are data, not 20 KB model-written contracts."""

import hashlib
import json
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.deterministic_verifier import DeterministicVerifier
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
    SchedulerArtifactError,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import (
    SchedulerAuditValidationError,
    _validate_frozen_diff,
)
from minicode.scheduler_trace import extract_scheduler_trace
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import task as task_module
from minicode.tools.edit_file import edit_file_tool
from minicode.types import AgentStep
from minicode.workflow_workspace import (
    WorkflowWorkspaceTransaction,
    _frozen_diff_document,
)

BASE = "sha256:" + "0" * 64
INITIAL = "def value():\n    return 0\n"
REGRESSION = "from candidate import value\ndef test_required_value():\n    assert value() == 3\n"


def _document(patch):
    return {"diffVersion": 1, "baseDigest": BASE,
            "patchDigest": "sha256:" + hashlib.sha256(patch).hexdigest(),
            "byteCount": len(patch), "complete": True, "encoding": "utf-8", "diff": patch.decode()}


def test_complete_diff_can_exceed_control_contract_size_without_losing_bytes():
    patch = (b'+"\\\\" comment\n' * 1800)
    expected = _document(patch)
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    document = _frozen_diff_document(patch=patch, base_digest=BASE, patch_digest=expected["patchDigest"])
    assert document == expected, "The complete patch must not become a preview merely to fit a control contract."
    artifact = store.publish("artifact_full_diff", document, authority=authority)
    assert len(artifact.content.encode()) > 20_000
    _validate_frozen_diff(json.loads(artifact.content), base_digest=BASE,
                          patch_digest=expected["patchDigest"], require_complete=True)


def test_model_assignment_cannot_use_full_diff_as_an_oversized_write():
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    channel = store.create_assignment_channel(readable_keys=(), write_key="artifact_model_result")
    document = _document(b"x" * 24_000)
    with pytest.raises(SchedulerArtifactError, match="20,000"):
        channel.write("artifact_model_result", json.dumps(document), author="executor")
    assert store.read("artifact_model_result") is None
    with pytest.raises(SchedulerArtifactError, match="authority"):
        store.publish("artifact_unauthorized_diff", document, authority=object())


def test_regular_host_contracts_keep_the_control_contract_limit():
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    with pytest.raises(SchedulerArtifactError, match="20,000"):
        store.publish("artifact_large_contract", {"payload": "x" * 24_000}, authority=authority)


def test_complete_patch_data_consumes_the_unchanged_shared_storage_budget():
    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    patch = b"x" * (MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES // 2)
    first = store.publish("artifact_first_diff", _document(patch), authority=authority)
    assert json.loads(first.content)["diff"].encode() == patch
    with pytest.raises(SchedulerArtifactError, match="4 MiB"):
        store.publish("artifact_second_diff", _document(patch), authority=authority)
    assert store.read("artifact_second_diff") is None
    overflow = _document(b"x" * MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES)
    with pytest.raises(SchedulerArtifactError, match="4 MiB"):
        SchedulerArtifactStore(scheduler_authority=authority).publish("artifact_overflow", overflow, authority=authority)
    with pytest.raises(SchedulerAuditValidationError):
        _validate_frozen_diff(overflow, base_digest=BASE, patch_digest=overflow["patchDigest"], require_complete=True)


def test_large_complete_diff_still_rejects_wrong_digest_count_or_shape():
    authority = object()
    original = _document(b"x" * 24_000)
    for mutation in ({"byteCount": 23_999}, {"byteCount": True}, {"complete": 1},
                     {"patchDigest": "sha256:" + "1" * 64}, {"extra": "field"}):
        document = {**original, **mutation}
        store = SchedulerArtifactStore(scheduler_authority=authority)
        with pytest.raises(SchedulerArtifactError):
            store.publish("artifact_invalid_diff", document, authority=authority)
        with pytest.raises(SchedulerAuditValidationError):
            _validate_frozen_diff(document, base_digest=BASE, patch_digest=original["patchDigest"], require_complete=True)


@pytest.mark.parametrize("forgery", ["over-total", "understated", "over-remaining"])
def test_durable_large_diff_read_preserves_index_and_total_bounds(tmp_path, forgery):
    from test_scheduler_audit_journal import _WORKFLOW_ID, _audit, _run_dir

    from minicode.run_journal import RunJournalStorageError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "journal"
    journal = RunJournal(workspace, data_dir=data_dir)
    run = journal.create_run(title="large diff index bounds", source="headless")
    audit = _audit(artifact_value=_document(b"x" * (MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES // 2)))
    journal.record_scheduler_audit(run.id, audit)
    directory = _run_dir(data_dir, run) / "scheduler-workflows" / _WORKFLOW_ID
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["artifacts"][0]
    if forgery == "over-total":
        entry["byteCount"] = MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES + 1
    elif forgery == "understated":
        entry["byteCount"] -= 1
    else:
        second = {**entry, "artifactId": "artifact_second_diff"}
        manifest["artifacts"].append(second)
        source = directory / "artifacts" / (entry["artifactId"] + ".json")
        (directory / "artifacts" / "artifact_second_diff.json").write_bytes(source.read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RunJournalStorageError, match="audit"):
        RunJournal(workspace, data_dir=data_dir).get_scheduler_audit(run.id, _WORKFLOW_ID)


def test_exact_19343_byte_patch_and_18_approved_criteria_reach_durable_commit(tmp_path, monkeypatch):
    target = tmp_path / "candidate.py"
    target.write_text(INITIAL)
    (tmp_path / "test_compat.py").write_text(
        "from candidate import value\ndef test_integer():\n    assert isinstance(value(), int)\n")
    replacement = "def value():\n" + "".join(
        f'    # "\\\\" row {i:04d} ' + "x" * 37 + "\n" for i in range(300)) + "    return 3\n"
    # Calibrate generic comment padding using the real Git snapshot format,
    # including both files. No live task/source/candidate is read or modified.
    transaction = WorkflowWorkspaceTransaction(str(tmp_path))
    try:
        (transaction.cwd / "candidate.py").write_text(replacement)
        (transaction.cwd / "test_regression.py").write_text(REGRESSION)
        frozen = transaction.freeze(allowed_paths=("candidate.py", "test_regression.py"), paths_relative_to_cwd=True)
        padding = 19_343 - frozen.diff_artifact_document["byteCount"]
        assert padding > 0
    finally:
        transaction.close()
    replacement = replacement.replace("row 0000 ", "row 0000 " + "x" * padding, 1)
    criteria = [{"id": f"criterion_{i}", "kind": "behavior", "text": f"Requirement {i}: value() returns integer 3."}
                for i in range(1, 19)]
    calls, commands, review_source_visible = [], [], []

    class Model:
        def __init__(self, role):
            self.role, self.count = role, 0

        def next(self, _messages, tools_override=None, **_kwargs):
            self.count += 1
            calls.append(self.role)
            if self.role == "general":
                if self.count == 1:
                    actions = [
                        {"id": "edit-source", "toolName": "edit_file", "input": {"path": "candidate.py", "old": INITIAL, "new": replacement}},
                        {"id": "add-test", "toolName": "executor_test", "input": {"path": "test_regression.py", "code": REGRESSION}},
                    ]
                else:
                    assert self.count == 2
                    count = tools_override.find("executor_submit").input_schema["properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                    actions = [{"id": "submit-execution", "toolName": "executor_submit", "input": {
                        "status": "completed", "steps": [{"step": i, "status": "completed"} for i in range(1, count + 1)],
                        "deviations": [], "blockers": [],
                    }}]
            else:
                if self.count == 1:
                    visible = "\n".join(item.get("content", "") for item in _messages)
                    review_source_visible.append("row 0299" in visible and "return 3" in visible)
                    actions = [{"id": "independent-review-probe", "toolName": "review_test", "input": {
                        "criterionIds": [item["id"] for item in criteria],
                        "scenario": "Independently execute the value() behavior shared by all stated requirements.",
                        "code": REGRESSION,
                    }}]
                else:
                    assert self.count == 2
                    receipt = json.loads(next(message["content"] for message in _messages
                                              if message.get("role") == "tool_result"
                                              and message.get("toolUseId") == "independent-review-probe"))
                    assert receipt["status"] == "passed" and receipt["evidence"]["exitCode"] == 0
                    actions = [{"id": "approve-review", "toolName": "review_submit", "input": {
                        "verdict": "approved", "criteria": [{"criterionId": item["id"], "status": "satisfied",
                            "rationale": "The independent behavioral probe passed on this frozen candidate.",
                            "probeIds": [receipt["probeId"]], "sourceEvidence": []} for item in criteria],
                        "blockingFindings": [], "warnings": [], "requestedChecks": [], "confidence": "high",
                    }}]
            return AgentStep(type="tool_calls", calls=actions)

    class TestSandbox:
        def wrap_command(self, *, argv, **_kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert target.read_text() == INITIAL
            result = super().run(**kwargs)
            commands.extend(item["exitCode"] for item in result["checks"])
            return result

    verifier = Verifier(sandbox_backend=TestSandbox(), explicit_trusted_executables={"python": sys.executable})
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", lambda _r, role, **_k: Model(role))
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([edit_file_tool]))
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context", lambda *_a, **_k: verifier)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    request = {"requestVersion": 1,
        "profile": {"taskId": "task_complete_diff", "kind": "change", "risk": "low", "complex": False, "parentHasSufficientContext": True},
        "scope": {"readRoots": ["."], "writePaths": ["candidate.py", "test_regression.py"], "forbiddenPaths": [".git", ".env"]},
        "criteria": criteria,
        "verificationChecks": [{"checkId": name, "kind": "tests", "argv": ["python", "-m", "pytest", "-q", path],
            "criterionIds": [item["id"] for item in criteria]} for name, path in (("compat", "test_compat.py"), ("regression", "test_regression.py"))]}
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(workspace=tmp_path, source="headless", title="complete frozen diff",
                     journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir)) as observation:
        result = task_module.task_tool.run({"description": "return required value", "prompt": "Return integer 3, add a regression test, and review the resulting change.",
            "agent_type": "workflow", "workflow_request": request}, ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"},
            _agent_budget=AgentTurnBudget(), _event_sink=observation, _central_workflow_v2_enforced=True))
        run_id = observation.run_id
    trace = extract_scheduler_trace(result.output)
    assert trace is not None
    journal = RunJournal(tmp_path, data_dir=data_dir)
    audit = journal.get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None
    documents = [json.loads(item.content) for item in audit.artifacts]
    review = next(doc for doc in documents if "verdict" in doc)
    assert review["verdict"] == "approved" and len(review["criteria"]) == 18
    assert all(item["status"] == "satisfied" for item in review["criteria"])
    diff = next(doc for doc in documents if "diffVersion" in doc)
    assert diff["byteCount"] == 19_343
    assert calls == ["general", "general", "plan", "plan"] and commands == [0, 0, 0, 0, 0]
    assert result.ok and trace["committed"], trace["terminalReason"]
    assert review_source_visible == [True]
    assert diff["complete"] is True and len(json.dumps(diff, ensure_ascii=False, separators=(",", ":")).encode()) > 20_000
    assert hashlib.sha256(diff["diff"].encode()).hexdigest() == diff["patchDigest"].removeprefix("sha256:")
    assert target.read_text() == replacement
    assert (tmp_path / "test_regression.py").read_text() == REGRESSION
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
