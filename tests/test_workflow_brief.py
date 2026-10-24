"""Workflow file transport preserves complete text and the existing authority."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

from minicode.permissions import PermissionDeniedError
from minicode.scheduler_permissions import SchedulerWorkerReadPermissions
from minicode.tooling import ToolContext, ToolResult
from minicode.tools import task as task_module
from minicode.workflow_brief import (
    prepare_workflow_brief,
    verify_workflow_brief,
    write_workflow_artifact,
)
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


def _input(prompt="@file:brief.md\nRepair the specified behavior."):
    return {
        "agent_type": "workflow", "description": "repair behavior",
        "prompt": prompt, "write_paths": ["example.py"],
        "verification_commands": [["python3", "-m", "pytest", "-q"]],
    }


def _capture(monkeypatch):
    calls = []

    def run(data, context):
        calls.append(data)
        return ToolResult(ok=True, output="observed")

    monkeypatch.setattr(task_module, "_SYNC_RUN", run)
    return calls


def test_long_body_reaches_all_criteria_and_digest_bound_snapshot(tmp_path, monkeypatch):
    clauses = [(f"Requirement {index}: " + "preserve behavior " * 90).strip() for index in range(8)]
    raw = "\r\n\r\n".join(clauses).encode("utf-8")
    assert len(raw) > 3800
    (tmp_path / "brief.md").write_bytes(raw)
    calls = _capture(monkeypatch)
    result = task_module.task_tool.run(
        task_module.task_tool.validator(_input()),
        ToolContext(cwd=str(tmp_path), _central_workflow_v2_enforced=True),
    )
    assert result.ok, result.output
    data, = calls
    brief = data["_workflow_brief"]
    assert brief.sha256 == hashlib.sha256(raw).hexdigest()
    assert Path(tmp_path, brief.path).read_bytes() == raw
    assert [item["text"] for item in data["workflow_request"]["criteria"]] == clauses
    assert brief.path in data["workflow_request"]["scope"]["readRoots"]
    assert len(data["prompt"]) < 3800 and clauses[-1] not in data["prompt"]
    assert brief.path in data["prompt"] and brief.sha256 in data["prompt"]
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        verify_workflow_brief(brief, transaction.cwd)
        assert (transaction.cwd / brief.path).read_bytes() == raw
    assert Path(tmp_path, brief.path).read_bytes() == raw


def test_exact_source_read_scope_only_gains_exact_derived_file(tmp_path, monkeypatch):
    (tmp_path / "brief.md").write_text("Keep the public behavior.")
    request = deepcopy(task_module._WORKFLOW_REQUEST_EXAMPLE)
    request["scope"]["readRoots"] = ["brief.md"]
    payload = {"agent_type": "workflow", "description": "repair behavior",
               "prompt": "@file:brief.md\nRepair behavior.", "workflow_request": request}
    calls = _capture(monkeypatch)
    result = task_module.task_tool.run(task_module.task_tool.validator(payload), ToolContext(cwd=str(tmp_path)))
    assert result.ok, result.output
    data, = calls
    scope = data["workflow_request"]["scope"]
    assert scope["readRoots"] == ["brief.md", data["_workflow_brief"].path]
    assert scope["writePaths"] == request["scope"]["writePaths"]
    permissions = SchedulerWorkerReadPermissions(
        str(tmp_path), read_roots=tuple(scope["readRoots"]),
        forbidden_paths=tuple(scope["forbiddenPaths"]),
    )
    permissions.ensure_path_access(str(tmp_path / data["_workflow_brief"].path), "read")
    with pytest.raises(PermissionDeniedError):
        permissions.ensure_path_access(str(tmp_path / "other.md"), "read")


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/brief.md", "C:\\brief.md", "", "a/../../brief.md"])
def test_reference_rejects_nonrelative_or_escaping_paths(path):
    with pytest.raises(ValueError, match="workspace-relative"):
        task_module.task_tool.validator(_input(f"@file:{path}\nSummary."))


def test_file_summary_and_inline_limits_remain_separate():
    task_module.task_tool.validator(_input("@file:brief.md\n" + "x" * 800))
    with pytest.raises(ValueError, match="800"):
        task_module.task_tool.validator(_input("@file:brief.md\n" + "x" * 801))
    with pytest.raises(ValueError, match="workflow_prompt_oversized"):
        task_module.task_tool.validator(_input("x" * 3801))


@pytest.mark.parametrize("denial", ["host_read", "host_write", "scope", "mandatory", "symlink"])
def test_source_authority_and_store_permissions_fail_closed(tmp_path, monkeypatch, denial):
    (tmp_path / "brief.md").write_text("Keep the behavior.")
    payload = _input()
    context = ToolContext(cwd=str(tmp_path), _central_workflow_v2_enforced=True)
    if denial.startswith("host_"):
        class DeniedPermissions:
            def ensure_path_access(self, target, intent):
                if intent == denial.removeprefix("host_"):
                    raise PermissionDeniedError("fixture access denied", code="fixture_denied")
        context.permissions = DeniedPermissions()
    elif denial == "scope":
        request = deepcopy(task_module._WORKFLOW_REQUEST_EXAMPLE)
        request["scope"]["readRoots"] = ["src"]
        payload = {key: value for key, value in payload.items()
                   if key not in {"write_paths", "verification_commands"}}
        payload["workflow_request"] = request
    elif denial == "mandatory":
        (tmp_path / ".env").write_text("Do not read.")
        payload["prompt"] = "@file:.env\nSummary."
    else:
        outside = tmp_path.parent / (tmp_path.name + "-outside.md")
        outside.write_text("Do not read.")
        (tmp_path / "escape.md").symlink_to(outside)
        payload["prompt"] = "@file:escape.md\nSummary."
    calls = _capture(monkeypatch)
    result = task_module.task_tool.run(task_module.task_tool.validator(payload), context)
    assert not result.ok and "workflow_brief_invalid" in result.output
    assert calls == []
    assert not (tmp_path / ".workflow-briefs").exists()


def test_file_changed_after_preparation_fails_digest_check(tmp_path):
    (tmp_path / "brief.md").write_text("Keep the original behavior.")
    brief = prepare_workflow_brief(_input(), ToolContext(cwd=str(tmp_path)))
    (tmp_path / "brief.md").write_text("A later request cannot replace frozen text.")
    verify_workflow_brief(brief, tmp_path)
    (tmp_path / brief.path).write_text("Tampered content.")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_workflow_brief(brief, tmp_path)


def test_artifact_write_preserves_large_diagnostics_atomically(tmp_path, monkeypatch):
    raw = ("diagnostic tail\n" * 25_000).encode()
    context = ToolContext(cwd=str(tmp_path))
    relative, digest = write_workflow_artifact(context, raw)
    assert (tmp_path / relative).read_bytes() == raw
    assert digest == hashlib.sha256(raw).hexdigest()

    def fail_replace(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("minicode.workflow_brief.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_workflow_artifact(context, b"new diagnostic")
    assert (tmp_path / relative).read_bytes() == raw
    assert list((tmp_path / ".workflow-briefs").glob(".pending-*")) == []


def test_private_brief_cannot_be_supplied_by_caller(tmp_path, monkeypatch):
    calls = _capture(monkeypatch)
    payload = _input("Keep the ordinary behavior.")
    payload["_workflow_brief"] = object()
    result = task_module.task_tool.run(payload, ToolContext(cwd=str(tmp_path)))
    assert result.ok
    assert "_workflow_brief" not in calls[0]


def test_generated_copy_does_not_override_explicit_forbidden_scope(tmp_path, monkeypatch):
    (tmp_path / "brief.md").write_text("Keep the public behavior.")
    request = deepcopy(task_module._WORKFLOW_REQUEST_EXAMPLE)
    request["scope"]["forbiddenPaths"].append(".workflow-briefs")
    payload = {"agent_type": "workflow", "description": "repair behavior",
               "prompt": "@file:brief.md\nRepair behavior.", "workflow_request": request}
    calls = _capture(monkeypatch)
    result = task_module.task_tool.run(task_module.task_tool.validator(payload), ToolContext(cwd=str(tmp_path)))
    assert not result.ok and "forbidden" in result.output
    assert calls == []


def test_artifact_store_symlink_cannot_redirect_authorized_text(tmp_path):
    destination = tmp_path / "redirected"
    destination.mkdir()
    (tmp_path / ".workflow-briefs").symlink_to(destination, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink"):
        write_workflow_artifact(ToolContext(cwd=str(tmp_path)), b"authorized source")
    assert list(destination.iterdir()) == []


def test_phase_archive_restores_source_cwd_with_original_parent_permissions(tmp_path):
    from minicode.permissions import PermissionManager

    source = tmp_path / "source"
    source.mkdir()
    parent = ToolContext(cwd=str(source), permissions=PermissionManager(str(source)))
    complete_output = b"complete phase output\n" * 1000
    with WorkflowWorkspaceTransaction(str(source)) as transaction:
        isolated = replace(parent, cwd=str(transaction.cwd))
        source_context = replace(isolated, cwd=str(transaction.source_cwd))
        assert source_context.permissions is parent.permissions
        path, digest = write_workflow_artifact(source_context, complete_output)
        assert (source / path).read_bytes() == complete_output
        assert not (transaction.cwd / path).exists()
    assert (source / path).read_bytes() == complete_output
    assert digest == hashlib.sha256(complete_output).hexdigest()


def test_actual_executor_reads_complete_brief_verifies_and_promotes(tmp_path, monkeypatch):
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.permissions import PermissionManager
    from minicode.run_journal import RunJournal
    from minicode.run_lifecycle import observe_run
    from minicode.scheduler_trace import extract_scheduler_trace
    from minicode.tooling import ToolRegistry
    from minicode.tools.edit_file import edit_file_tool
    from minicode.tools.read_file import read_file_tool
    from minicode.types import AgentStep
    from test_workflow_v2_structured_routing import _prompt_json

    initial = "def value():\n    return 0\n"
    expected = "def value():\n    return 7\n"
    (tmp_path / "example.py").write_text(initial)
    (tmp_path / "test_example.py").write_text(
        "from example import value\ndef test_value():\n    assert value() == 7\n"
    )
    clauses = [f"Requirement {index}: " + ("Keep value() returning integer 7. " * 25).strip()
               for index in range(6)]
    clauses.append("FINAL_BODY_REQUIREMENT: Preserve the public function name value.")
    body = "\n\n".join(clauses)
    assert 3800 < len(body) < 8000
    (tmp_path / "brief.md").write_text(body)
    assignments, verification_workspaces, read_receipts, worker_errors = [], [], [], []
    original_worker = task_module._run

    def observe_worker(data, context):
        assert context._logical_agent_role == "executor"
        view = _prompt_json(data["prompt"], "EXECUTOR ASSIGNMENT")
        artifact = context._subagent_mailbox.read(view["fullAssignmentArtifactRef"])
        assignment = json.loads(artifact.content)["assignment"]
        assignments.append(assignment)
        assert context.cwd != str(tmp_path)
        assert len(assignment["objective"]) < 3800
        assert clauses[-1] not in assignment["objective"]
        assert [item["text"] for item in assignment["acceptanceCriteria"]] == clauses
        return original_worker(data, context)

    def checked_worker(data, context):
        try:
            return observe_worker(data, context)
        except Exception as exc:
            worker_errors.append(f"{type(exc).__name__}: {exc}")
            raise

    class Executor:
        calls = 0
        tools = None

        def next(self, messages, tools_override=None, **kwargs):
            self.calls += 1
            tools = tools_override or self.tools
            if self.calls == 1:
                path = re.search(r"\.workflow-briefs/[0-9a-f]{64}\.md", str(messages)).group(0)
                calls = [
                    {"id": "read-brief", "toolName": "read_file", "input": {"path": path}},
                    {"id": "read-example", "toolName": "read_file", "input": {"path": "example.py"}},
                ]
            elif self.calls == 2:
                receipt = next(message for message in messages
                               if message.get("toolUseId") == "read-brief"
                               and message.get("role") == "tool_result")
                assert not receipt.get("isError") and body in receipt["content"]
                read_receipts.append(receipt["content"])
                calls = [
                    {"id": "edit-example", "toolName": "edit_file",
                     "input": {"path": "example.py", "old": initial, "new": expected}},
                    {"id": "verify-example", "toolName": "executor_check", "input": {"check": 1}},
                ]
            else:
                assert self.calls == 3, messages[-3:]
                for receipt in messages:
                    if receipt.get("role") == "tool_result" and receipt.get("toolUseId") in {
                        "edit-example", "verify-example",
                    }:
                        assert not receipt.get("isError"), receipt["content"]
                count = tools.find("executor_submit").input_schema[
                    "properties"]["steps"]["items"]["properties"]["step"]["maximum"]
                calls = [{"id": "submit-example", "toolName": "executor_submit", "input": {
                    "status": "completed", "steps": [
                        {"step": step, "status": "completed"} for step in range(1, count + 1)],
                    "deviations": [], "blockers": [],
                }}]
            return AgentStep(type="tool_calls", calls=calls)

    executor = Executor()

    def factory(runtime, agent_type, **kwargs):
        assert agent_type == "general"
        executor.tools = kwargs["tools"]
        return executor

    class TestSandbox:
        def wrap_command(self, *, argv, **kwargs):
            return argv

    class Verifier(DeterministicVerifier):
        def run(self, **kwargs):
            assert (tmp_path / "example.py").read_text() == initial
            verification_workspaces.append(kwargs["cwd"])
            return super().run(**kwargs)

    verifier = Verifier(sandbox_backend=TestSandbox(),
                        explicit_trusted_executables={"python3": sys.executable})
    monkeypatch.setattr(task_module, "_run", checked_worker)
    monkeypatch.setattr("minicode.subagent_model_routing.create_subagent_model_adapter", factory)
    monkeypatch.setattr("minicode.tools.create_default_tool_registry",
                        lambda *args, **kwargs: ToolRegistry([read_file_tool, edit_file_tool]))
    monkeypatch.setattr("minicode.workflow_runner._deterministic_verifier_for_context",
                        lambda *args, **kwargs: verifier)
    monkeypatch.setattr("minicode.subagent_types._read_max_turns_override", lambda name: None)
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *args, **kwargs: None)
    monkeypatch.setattr("minicode.intent_semantic_classifier.build_semantic_intent_adapter",
                        lambda *args, **kwargs: None)
    payload = _input()
    payload["verification_commands"] = [["python3", "-m", "pytest", "-q", "test_example.py"]]
    data_dir = tmp_path.parent / (tmp_path.name + "-journal")
    with observe_run(
        workspace=tmp_path, source="headless", title="workflow brief integration",
        journal_factory=lambda workspace: RunJournal(workspace, data_dir=data_dir),
    ) as observation:
        result = task_module.task_tool.run(
            task_module.task_tool.validator(payload),
            ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"},
                        _event_sink=observation, _central_workflow_v2_enforced=True),
        )
        run_id = observation.run_id
    assert result.ok, result.output + "\nWorker errors: " + repr(worker_errors)
    trace = extract_scheduler_trace(result.output)
    assert trace["committed"] and trace["ruleId"] == "route.verified_change"
    audit = RunJournal(tmp_path, data_dir=data_dir).get_scheduler_audit(run_id, trace["workflowId"])
    assert audit is not None
    assert (tmp_path / "example.py").read_text() == expected
    assert len(assignments) == len(read_receipts) == 1 and executor.calls == 3
    assert verification_workspaces and all(not Path(path).exists() for path in verification_workspaces)
    assert next((tmp_path / ".workflow-briefs").glob("*.md")).read_text() == body


def test_snapshot_digest_mismatch_prevents_worker_and_parent_write(tmp_path, monkeypatch):
    (tmp_path / "brief.md").write_text("Set the public value to 7.")
    (tmp_path / "example.py").write_text("unchanged")

    class TamperedTransaction(WorkflowWorkspaceTransaction):
        def _snapshot_directory(self):
            super()._snapshot_directory()
            path = next((self.workspace_root / ".workflow-briefs").glob("*.md"))
            path.write_text("Snapshot brief was altered.")

    monkeypatch.setattr("minicode.workflow_runner._WorkflowWorkspaceTransaction", TamperedTransaction)
    monkeypatch.setattr(task_module, "_run", lambda *args: pytest.fail("worker ran after digest mismatch"))
    result = task_module.task_tool.run(
        task_module.task_tool.validator(_input()),
        ToolContext(cwd=str(tmp_path), _runtime={"model": "local-fixture"},
                    _central_workflow_v2_enforced=True),
    )
    assert not result.ok and "workflow_isolation_unavailable" in result.output
    assert "digest mismatch" in result.output
    assert (tmp_path / "example.py").read_text() == "unchanged"


@pytest.mark.parametrize("instruction,expected_roles,expected_route", [
    ("Analyze first.", ["analyst", "executor"], "route.analyzed_change"),
    ("Review the resulting change.", ["executor", "reviewer"], "route.scoped_change"),
    ("Analyze first. Review the resulting change.",
     ["analyst", "executor", "reviewer"], "route.reviewed_change"),
])
def test_body_only_role_instructions_control_the_real_workflow_route(
    tmp_path, monkeypatch, instruction, expected_roles, expected_route,
):
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.scheduler_audit import project_scheduler_audit
    from minicode.scheduler_trace import extract_scheduler_trace
    from test_workflow_v2_structured_routing import (
        _request, _run, _verification_result, _worker_role,
        _write_analyst_result, _write_executor_declaration, _write_reviewer_result,
    )

    body = "Change allowed.txt from before to after.\n" + instruction
    (tmp_path / "brief.md").write_text(body)
    (tmp_path / "allowed.txt").write_text("before")
    roles, audits = [], []

    class Sink:
        def emit(self, *_args, **_kwargs):
            pass

        def record_scheduler_audit(self, audit):
            audits.append(audit)
            return True

        def record_scheduler_commit_ready(self, _ready):
            return True

    def worker(data, context):
        role = _worker_role(data)
        roles.append(role)
        if role == "analyst":
            _write_analyst_result(data, context, prepare_change=True)
        elif role == "executor":
            assert (tmp_path / "allowed.txt").read_text() == "before"
            Path(context.cwd, "allowed.txt").write_text("after")
            _write_executor_declaration(data, context)
        else:
            _write_reviewer_result(data, context)
        return ToolResult(ok=True, output="Bound role completed.")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", lambda _self, **values: _verification_result(values))
    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch,
                  event_sink=Sink(), prompt="@file:brief.md\nApply the requested change.")
    assert result.ok, result.output
    assert roles == expected_roles
    assert extract_scheduler_trace(result.output)["ruleId"] == expected_route
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert (tmp_path / "brief.md").read_text() == body
    assert len(audits) == 1
    audit = audits[0]
    assert project_scheduler_audit(profile=audit.profile, plan=audit.plan, trace=audit.trace,
                                   artifacts=audit.artifacts, contract_mode="v2") == audit
