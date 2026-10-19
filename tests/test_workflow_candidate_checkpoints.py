"""Executor candidates survive abrupt exits before final cleanup can run."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from minicode.subagent_journal import (
    SubagentJournalError, SubagentRunJournal, new_subagent_id,
)


_EXIT_AFTER_EXECUTOR = textwrap.dedent("""\
    import os
    import sys
    from pathlib import Path
    from unittest.mock import patch
    from minicode.deterministic_verifier import DeterministicVerifier
    from minicode.subagent_journal import SubagentRunJournal
    from minicode.tooling import ToolResult
    import minicode.tools.task as task_module
    from test_workflow_v2_structured_routing import (
        _request, _run, _verification_result, _worker_role,
        _write_executor_declaration,
    )

    workspace, run_dir, marker, exit_stage = sys.argv[1:]
    journal = SubagentRunJournal(Path(run_dir))

    class Sink:
        def open_subagent_journal(self):
            return journal
        def emit(self, *args, **kwargs):
            pass
        def record_scheduler_audit(self, audit):
            Path(marker).with_suffix('.audit').write_text('audit reached')
            return True

    def hard_exit(stage):
        Path(marker).write_text(stage)
        os._exit(73)  # Bypass every Python finally and transaction cleanup.

    def worker(data, context):
        role = _worker_role(data)
        if role == 'executor':
            Path(context.cwd, 'allowed.txt').write_text('candidate before review')
            _write_executor_declaration(data, context)
            return ToolResult(ok=True, output='executor completed')
        assert role == 'reviewer' and exit_stage == 'reviewer'
        hard_exit(role)

    def verifier(self, **request):
        if exit_stage == 'verifier':
            hard_exit('verifier')
        return _verification_result(request)

    with patch.object(task_module, '_run', worker), patch.object(DeterministicVerifier, 'run', verifier):
        _run(Path(workspace), _request(risk='low', complex=False), None,
             event_sink=Sink(), commit_audit_waived=True)
    raise AssertionError('next stage was not reached')
""")


@pytest.mark.parametrize("exit_stage", ["verifier", "reviewer"])
def test_executor_transition_persists_before_next_stage_hard_exit(tmp_path, exit_stage):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("before")
    (workspace / "outside.txt").write_text("OUTSIDE_WRITE_SCOPE")
    run_dir = tmp_path / ("run_" + "1" * 32)
    run_dir.mkdir()
    child_temp = tmp_path / "child-temp"
    child_temp.mkdir()
    marker = tmp_path / "stage.txt"
    repo = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, TMPDIR=str(child_temp),
                       PYTHONPATH=os.pathsep.join((str(repo), str(repo / "tests"))))
    process = subprocess.run(
        [sys.executable, "-c", _EXIT_AFTER_EXECUTOR, str(workspace),
         str(run_dir), str(marker), exit_stage],
        cwd=repo, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 73, process.stdout + process.stderr
    assert marker.read_text() == exit_stage
    checkpoints = list(run_dir.rglob("candidate-checkpoint-*.json"))
    assert len(checkpoints) == 1, "next stage killed the only candidate before finally"
    saved = json.loads(checkpoints[0].read_text())
    assert saved["promotable"] is False
    assert saved["checkpoint"]["stage"] == "executor"
    assert saved["checkpoint"]["sequence"] > 0
    assert saved["checkpoint"]["agentRunId"].startswith("sub_")
    assert {entry["path"] for entry in saved["files"]} == {"allowed.txt"}
    assert saved["files"][0]["content"] == "candidate before review"
    assert "OUTSIDE_WRITE_SCOPE" not in checkpoints[0].read_text()
    assert checkpoints[0].stat().st_mode & 0o777 == 0o600
    assert not list(run_dir.rglob("failure-candidate.json")), "outer finally unexpectedly ran"
    assert not marker.with_suffix(".audit").exists()
    assert (workspace / "allowed.txt").read_text() == "before"


def test_repair_checkpoints_do_not_occupy_final_failure_candidate(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner
    from test_workflow_implementation_repair import _repair_run

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_dir = tmp_path / ("run_" + "2" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    monkeypatch.setattr(runner, "_open_subagent_journal", lambda _context: journal)
    result, roles, _inputs, _audits = _repair_run(
        workspace, monkeypatch, rejection="verifier", always_bad=True,
    )
    assert not result.ok
    assert roles.count("executor") == 2
    checkpoints = sorted(
        (json.loads(path.read_text()) for path in run_dir.rglob("candidate-checkpoint-*.json")),
        key=lambda value: value["checkpoint"]["sequence"],
    )
    assert len(checkpoints) == 2
    assert [value["files"][0]["content"] for value in checkpoints] == ["almost", "still-wrong"]
    assert all(value["promotable"] is False for value in checkpoints)
    final_paths = list(run_dir.rglob("failure-candidate.json"))
    assert len(final_paths) == 1
    final = json.loads(final_paths[0].read_text())
    assert final["promotable"] is False
    assert final["files"][0]["content"] == "still-wrong"
    assert final["files"][0]["content"] != checkpoints[0]["files"][0]["content"]
    receipt = json.loads(result.output.splitlines()[1])
    assert receipt["candidateRetained"] is True
    assert receipt["parentWriteback"] == "not_applied"
    assert (workspace / "allowed.txt").read_text() == "before"


def test_checkpoint_persistence_error_cannot_grant_writeback(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner
    from test_workflow_implementation_repair import _repair_run

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_dir = tmp_path / ("run_" + "3" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    persist = journal.persist_failure_candidate
    attempts = []

    def fail_checkpoint(*args, **kwargs):
        if kwargs.get("checkpoint_revision") is not None:
            attempts.append(kwargs["checkpoint_revision"])
            raise OSError("checkpoint disk failure")
        return persist(*args, **kwargs)

    monkeypatch.setattr(journal, "persist_failure_candidate", fail_checkpoint)
    monkeypatch.setattr(runner, "_open_subagent_journal", lambda _context: journal)
    result, roles, _inputs, _audits = _repair_run(
        workspace, monkeypatch, rejection="verifier", always_bad=True,
    )
    assert len(attempts) == 2 and roles.count("executor") == 2
    assert not result.ok
    assert not list(run_dir.rglob("candidate-checkpoint-*.json"))
    assert len(list(run_dir.rglob("failure-candidate.json"))) == 1
    assert (workspace / "allowed.txt").read_text() == "before"


@pytest.mark.parametrize("revision", [0, -1, 1000000, True, "../escape", "1", 1.5])
def test_checkpoint_revision_cannot_choose_a_path(tmp_path, revision):
    run_dir = tmp_path / ("run_" + "4" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    with pytest.raises(SubagentJournalError, match="checkpoint revision"):
        journal.persist_failure_candidate(
            new_subagent_id(), {"failureCandidateVersion": 1, "promotable": False, "files": []},
            checkpoint_revision=revision,
        )
    assert not list(run_dir.rglob("*.json"))


def test_checkpoint_is_immutable_and_cannot_follow_destination_symlink(tmp_path):
    run_dir = tmp_path / ("run_" + "5" * 32)
    run_dir.mkdir()
    journal = SubagentRunJournal(run_dir)
    child = new_subagent_id()
    candidate = {"failureCandidateVersion": 1, "promotable": False, "files": []}
    checkpoint = journal.persist_failure_candidate(child, candidate, checkpoint_revision=2)
    with pytest.raises(FileExistsError):
        journal.persist_failure_candidate(child, candidate, checkpoint_revision=2)
    outside = tmp_path / "untouched.json"
    outside.write_text("untouched")
    checkpoint.with_name("candidate-checkpoint-3.json").symlink_to(outside)
    with pytest.raises(OSError):
        journal.persist_failure_candidate(child, candidate, checkpoint_revision=3)
    assert outside.read_text() == "untouched"
