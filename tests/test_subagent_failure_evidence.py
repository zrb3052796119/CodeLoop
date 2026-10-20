"""Failures keep actual child tool evidence and a non-promotable candidate."""

import json
from pathlib import Path

import pytest

from minicode import subagent_model_routing
from minicode.agent_budget import AgentTurnBudget
from minicode.subagent_journal import SubagentRunJournal, new_subagent_id
from minicode.tooling import ToolContext, ToolRegistry, ToolResult
from minicode.tools.read_file import read_file_tool
from minicode.tools.task import task_tool
from minicode.types import AgentStep, ModelAdapter
from minicode.workflow_workspace import WorkflowWorkspaceTransaction


@pytest.mark.parametrize("broken_diagnostics", [False, True])
def test_real_headless_child_keeps_failed_tool_result_before_budget_exit(tmp_path, monkeypatch, broken_diagnostics):
    import minicode.tools.task as task_module

    assert Path(task_module.__file__).resolve().parents[2] == Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_dir = tmp_path / ("run_" + "1" * 32)
    run_dir.mkdir()
    if broken_diagnostics:
        (run_dir / "subagent-runs").mkdir()
        (run_dir / "subagent-runs/tool-evidence").write_text("blocked destination")
    journal = SubagentRunJournal(run_dir)
    child = new_subagent_id()

    class Sink:
        def open_subagent_journal(self):
            return journal

        def emit(self, *_a, **_k):
            pass

    class Model(ModelAdapter):
        calls = 0

        def next(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AgentStep(type="tool_calls", calls=[{
                    "id": "missing", "toolName": "read_file",
                    "input": {"path": "missing.py"},
                }])
            raise AssertionError("exhausted budget must prevent a second provider call")

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_a, **_k: ToolRegistry([read_file_tool]))
    monkeypatch.setattr(subagent_model_routing, "create_subagent_model_adapter", lambda *_a, **_k: Model())
    result = task_tool.run({"description": "read missing code", "prompt": "Read missing.py",
                            "agent_type": "explore", "_subagent_id": child},
                           ToolContext(cwd=str(workspace), _runtime={"model": "fake"}, _event_sink=Sink(),
                                       _agent_budget=AgentTurnBudget(max_model_calls=1, max_total_tokens=500000)))
    assert not result.ok
    assert journal.list_runs()[0].outcome == "budget_exceeded", result.output
    if broken_diagnostics:
        assert journal.list_runs()[0].tool_calls == 1
        assert not list(run_dir.rglob("*.tools.jsonl"))
        return
    evidence_files = list(run_dir.rglob("*.tools.jsonl"))
    assert evidence_files, "headless Task discarded actual child tool requests and results"
    assert journal.list_runs()[0].tool_calls == 1
    evidence = [json.loads(line) for line in evidence_files[0].read_text().splitlines()]
    assert [item["type"] for item in evidence] == ["tool.started", "tool.finished"]
    assert evidence[0]["invocationId"] == evidence[1]["invocationId"]
    assert evidence[0]["input"]["path"] == "missing.py"
    assert evidence[1]["isError"] is True
    assert "error[not_found]" in evidence[1]["output"]
    assert "missing.py" not in repr(journal.list_events(child))


def test_failed_candidate_survives_cleanup_without_promoting(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("value = 1\n")
    run_dir = tmp_path / ("run_" + "2" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    child = new_subagent_id()
    with WorkflowWorkspaceTransaction(str(workspace), baseline_scope_paths=("app.py", "tests")) as transaction:
        isolated = transaction.workspace_root
        (isolated / "app.py").write_text("value = 2\n", newline="\n")
        (isolated / "tests").mkdir()
        (isolated / "tests/test_new.py").write_text("def test_value(): assert False\n", newline="\n")
        (isolated / "outside.py").write_text("UNAUTHORIZED_BYTES\n")
        # Diagnostics must still run after the operation deadline expires.
        transaction.set_operation_guard(deadline_monotonic=0, timeout_seconds=1, operation_checkpoint=None)
        capture = getattr(transaction, "capture_failure_candidate", None)
        assert callable(capture), "candidate is deleted on failed workflow exit"
        candidate = capture(("app.py", "tests"), paths_relative_to_cwd=True)
        path = journal.persist_failure_candidate(child, candidate)
    assert not isolated.exists()
    assert (workspace / "app.py").read_text() == "value = 1\n"
    saved = json.loads(path.read_text())
    assert saved["promotable"] is False
    assert {item["path"] for item in saved["files"]} == {"app.py", "tests/test_new.py"}
    assert saved["files"][0]["content"] == "value = 2\n"
    assert "UNAUTHORIZED_BYTES" not in path.read_text()
    assert "-value = 1" in saved["files"][0]["diff"]
    assert "+value = 2" in saved["files"][0]["diff"]
    with pytest.raises(FileExistsError):
        journal.persist_failure_candidate(child, candidate)


def test_diagnostics_omit_symlink_binary_oversize_and_redact_secrets(tmp_path):
    from minicode.failure_evidence import MAX_FILE_BYTES

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("value = 1\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("NEVER_CAPTURE_OUTSIDE\n")
    with WorkflowWorkspaceTransaction(str(workspace), baseline_scope_paths=(".",)) as transaction:
        isolated = transaction.workspace_root
        (isolated / "link.py").symlink_to(secret)
        (isolated / "linked").symlink_to(tmp_path)
        (isolated / "binary.dat").write_bytes(b"\x00PRIVATE_BINARY")
        (isolated / "huge.py").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        (isolated / "app.py").write_text('api_key = "sk-private123456789"\nvalue = 2\n')
        (isolated / ".env").write_text("NEVER_CAPTURE_ENV\n")
        capture = transaction.capture_failure_candidate((".",))
        serialized = json.dumps(capture)
        assert "NEVER_CAPTURE_OUTSIDE" not in serialized
        assert "NEVER_CAPTURE_ENV" not in serialized
        assert "PRIVATE_BINARY" not in serialized
        assert "sk-private123456789" not in serialized
        entries = {item["path"]: item for item in capture["files"]}
        assert entries["link.py"]["omitted"] == "symlink"
        assert entries["binary.dat"]["omitted"] == "binary"
        assert entries["huge.py"]["omitted"] == "size_limit"
        assert entries["app.py"]["redacted"]
        assert capture["complete"] is False
        nested = transaction.capture_failure_candidate(("linked/secret.txt",))
        assert nested["files"][0]["omitted"] == "unsafe_ancestor"


def test_tool_evidence_is_bounded_redacted_and_keeps_failure_tail(tmp_path):
    root = tmp_path / ("run_" + "3" * 32)
    root.mkdir()
    journal = SubagentRunJournal(root)
    child = new_subagent_id()
    journal.append_tool_evidence(child, invocation_id="edit-1", tool_name="write_file", phase="started",
                                 tool_input={"path": "app.py", "api_key": "do-not-save", "image_url": "data:image/png;base64,QUJDRA=="})
    journal.append_tool_evidence(child, invocation_id="edit-1", tool_name="write_file", phase="finished",
                                 output="Authorization: Bearer private-key-value\n" + "ordinary log line\n" * 3000 + "\nFAILED tests/test_x.py: AssertionError", is_error=True)
    path = next(root.rglob("*.tools.jsonl"))
    raw = path.read_text()
    assert "do-not-save" not in raw and "QUJDRA" not in raw and "private-key-value" not in raw
    result = json.loads(raw.splitlines()[-1])
    assert result["output"].endswith("FAILED tests/test_x.py: AssertionError")
    assert result["outputTruncated"]
    assert len(result["output"]) <= 16000
    assert path.stat().st_mode & 0o777 == 0o600


def test_diagnostic_destination_symlinks_cannot_escape_or_overwrite(tmp_path):
    root = tmp_path / ("run_" + "4" * 32)
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "scheduler-workflows").symlink_to(outside, target_is_directory=True)
    journal = SubagentRunJournal(root)
    candidate = {"failureCandidateVersion": 1, "promotable": False, "files": []}
    child = new_subagent_id()
    with pytest.raises(OSError):
        journal.persist_failure_candidate(child, candidate)
    assert not list(outside.iterdir())
    (root / "subagent-runs").mkdir()
    (root / "subagent-runs/tool-evidence").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        journal.append_tool_evidence(child, invocation_id="test", tool_name="read_file", phase="started")
    assert not list(outside.iterdir())


@pytest.mark.parametrize("budget_abort", [False, True])
def test_real_failed_workflow_preserves_candidate_before_cleanup(tmp_path, monkeypatch, budget_abort):
    import minicode.tools.task as task_module
    import minicode.workflow_runner as runner
    from minicode.scheduler_trace import extract_scheduler_trace
    from minicode.subagent_result import extract_subagent_result
    from test_workflow_v2_structured_routing import _request, _run, _worker_role

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("before")
    run_dir = tmp_path / ("run_" + "5" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    child_workspaces = []
    original_outputs = []
    observed_bases = []
    run_phases = runner._run_workflow_phases

    def record_phase_output(*args, **kwargs):
        result = run_phases(*args, **kwargs)
        original_outputs.append(result.output)
        return result

    monkeypatch.setattr(runner, "_run_workflow_phases", record_phase_output)

    class Sink:
        def open_subagent_journal(self):
            return journal

        def emit(self, *_a, **_k):
            pass

        def record_scheduler_audit(self, _audit):
            return True

    def worker(data, context):
        assert _worker_role(data) == "executor"
        candidate = Path(context.cwd)
        child_workspaces.append(candidate)
        observed_bases.append((candidate / "allowed.txt").read_text())
        (candidate / "allowed.txt").write_text("failed but inspectable")
        if budget_abort:
            exhausted = AgentTurnBudget(max_model_calls=1, max_total_tokens=10000)
            exhausted.reserve_model_call(1)
            exhausted.reserve_model_call(1)
        return ToolResult(ok=False, output="implementation did not satisfy tests")

    monkeypatch.setattr(task_module, "_run", worker)
    result = _run(workspace, _request(risk="low", complex=False), monkeypatch, event_sink=Sink())
    assert not result.ok, result.output
    assert child_workspaces and all(not path.exists() for path in child_workspaces)
    assert (workspace / "allowed.txt").read_text() == "before"
    saved_paths = list(run_dir.rglob("failure-candidate.json"))
    assert len(saved_paths) == 1, "actual workflow cleanup discarded the failed implementation"
    saved = json.loads(saved_paths[0].read_text())
    assert saved["promotable"] is False
    assert saved["files"][0]["content"] == "failed but inspectable"
    assert "+failed but inspectable" in saved["files"][0]["diff"]
    marker, raw_receipt, original_output = result.output.split("\n", 2)
    assert marker == "=== WORKFLOW WORKSPACE STATE v1 ==="
    receipt = json.loads(raw_receipt)
    assert receipt == {
        "workspaceStateVersion": 1,
        "workflowId": saved_paths[0].parent.name,
        "parentWriteback": "not_applied",
        "candidateRetained": True,
        "candidate": {
            "artifactRef": saved_paths[0].relative_to(run_dir).as_posix(),
            "promotable": False,
        },
        "nextWorkflowBase": "parent_current",
    }
    assert original_output == original_outputs[0]
    assert len(result.output) - len(original_output) <= 512
    assert extract_scheduler_trace(result.output) is not None
    assert extract_subagent_result(result.output) is not None
    finalization = json.loads(original_output.split("=== WORKFLOW FINALIZATION v1 ===\n", 1)[1].split("\n", 1)[0])
    assert finalization["changesCommitted"] is False
    assert "failed but inspectable" not in raw_receipt

    # A following workflow reads the real parent baseline, never the retained
    # diagnostic candidate from the previous failed invocation.
    second = _run(workspace, _request(risk="low", complex=False), monkeypatch, event_sink=Sink())
    assert not second.ok
    assert observed_bases == ["before", "before"]


@pytest.mark.parametrize("diagnostics", ["persist_error", "no_journal"])
def test_failed_workflow_receipt_does_not_claim_missing_candidate(tmp_path, monkeypatch, diagnostics):
    import minicode.tools.task as task_module
    from test_workflow_v2_structured_routing import _request, _run

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("before")
    run_dir = tmp_path / ("run_" + "6" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)

    def fail_persist(*_args, **_kwargs):
        raise OSError("diagnostic disk failure")

    monkeypatch.setattr(journal, "persist_failure_candidate", fail_persist)

    class Sink:
        def open_subagent_journal(self):
            return journal if diagnostics == "persist_error" else None

        def emit(self, *_a, **_k):
            pass

        def record_scheduler_audit(self, _audit):
            return True

    def worker(_data, context):
        Path(context.cwd, "allowed.txt").write_text("failed candidate")
        return ToolResult(ok=False, output="implementation failed")

    monkeypatch.setattr(task_module, "_run", worker)
    result = _run(workspace, _request(risk="low", complex=False), monkeypatch, event_sink=Sink())
    assert not result.ok
    marker, raw_receipt, _original_output = result.output.split("\n", 2)
    assert marker == "=== WORKFLOW WORKSPACE STATE v1 ==="
    receipt = json.loads(raw_receipt)
    assert receipt["parentWriteback"] == "not_applied"
    assert receipt["candidateRetained"] is False
    assert receipt["candidate"] is None
    assert receipt["nextWorkflowBase"] == "parent_current"
    assert not list(run_dir.rglob("failure-candidate.json"))
    assert (workspace / "allowed.txt").read_text() == "before"


def test_committed_workflow_never_returns_failed_workspace_receipt(tmp_path, monkeypatch):
    import minicode.tools.task as task_module
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.subagent_result import extract_subagent_result
    from test_workflow_v2_structured_routing import (
        _request, _run, _verification_result, _write_executor_declaration,
        _worker_role, _write_reviewer_result,
    )

    (tmp_path / "allowed.txt").write_text("before")

    def worker(data, context):
        if _worker_role(data) == "executor":
            Path(context.cwd, "allowed.txt").write_text("after")
            _write_executor_declaration(data, context)
        else:
            assert _worker_role(data) == "reviewer"
            _write_reviewer_result(data, context)
        return ToolResult(ok=True, output="implementation complete")

    monkeypatch.setattr(task_module, "_run", worker)
    monkeypatch.setattr(DeterministicVerifier, "run", lambda self, **request: _verification_result(request))
    result = _run(tmp_path, _request(risk="low", complex=False), monkeypatch, commit_audit_waived=True)
    assert result.ok, result.output
    assert (tmp_path / "allowed.txt").read_text() == "after"
    assert "=== WORKFLOW WORKSPACE STATE v1 ===" not in result.output
    assert extract_subagent_result(result.output)["outcome"] == "completed"


def test_failed_workspace_diagnostics_preserve_original_exception(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner
    from test_workflow_v2_structured_routing import _request, _run

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("before")
    run_dir = tmp_path / ("run_" + "7" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    original_error = RuntimeError("original phase failure")
    child_workspaces = []

    class Sink:
        def open_subagent_journal(self):
            return journal

        def emit(self, *_a, **_k):
            pass

    def fail_phases(_data, context, *_args, **_kwargs):
        child_workspaces.append(Path(context.cwd))
        Path(context.cwd, "allowed.txt").write_text("failed candidate")
        raise original_error

    def fail_persist(*_args, **_kwargs):
        raise OSError("secondary diagnostic error")

    monkeypatch.setattr(runner, "_run_workflow_phases", fail_phases)
    monkeypatch.setattr(journal, "persist_failure_candidate", fail_persist)
    with pytest.raises(RuntimeError) as caught:
        _run(workspace, _request(risk="low", complex=False), monkeypatch, event_sink=Sink())
    assert caught.value is original_error
    assert child_workspaces and all(not path.exists() for path in child_workspaces)
    assert (workspace / "allowed.txt").read_text() == "before"
