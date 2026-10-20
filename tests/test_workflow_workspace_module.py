"""Architecture seam for the extracted workflow workspace transaction."""

from __future__ import annotations

import ast
import stat
from pathlib import Path

import pytest

from minicode import workflow_runner, workflow_workspace
from minicode.task_deadline import TaskDeadlineExceeded
from minicode.tools import task as task_module


def test_runner_compatibility_names_share_the_extracted_types() -> None:
    assert (
        workflow_runner._WorkflowWorkspaceTransaction
        is workflow_workspace.WorkflowWorkspaceTransaction
    )
    assert (
        workflow_runner._WorkflowIsolationError
        is workflow_workspace.WorkflowIsolationError
    )
    assert not hasattr(task_module, "_WorkflowWorkspaceTransaction")
    assert not hasattr(task_module, "_WorkflowIsolationError")
    assert task_module._TaskDeadlineExceeded is TaskDeadlineExceeded


def test_shared_task_deadline_preserves_the_existing_contract() -> None:
    error = TaskDeadlineExceeded(1.25)

    assert error.timeout_seconds == 1.25
    assert str(error) == "sub-agent deadline exceeded after 1.25s"


def test_workspace_module_exports_both_transaction_states() -> None:
    assert set(workflow_workspace.__all__) == {
        "FrozenWorkflowChange",
        "WorkflowIsolationError",
        "WorkflowWorkspaceTransaction",
    }


def test_workspace_module_does_not_depend_on_the_task_orchestrator() -> None:
    module_path = Path(workflow_workspace.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    assert "minicode.tools.task" not in imported_modules


def test_windows_readonly_git_object_is_deleted_after_clearing_the_bit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "object"
    target.write_bytes(b"blob")
    target.chmod(stat.S_IREAD | stat.S_IWRITE)
    attempts = {"n": 0}
    original = Path.unlink

    def unlink(self, missing_ok=False):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise PermissionError(5, "Access is denied")
        return original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(workflow_workspace, "_host_is_windows", lambda: True)
    workflow_workspace._delete_workspace_entry(target)
    assert attempts["n"] == 2
    assert not target.exists()


def test_posix_permission_error_while_deleting_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "object"
    target.write_bytes(b"blob")
    monkeypatch.setattr(workflow_workspace, "_host_is_windows", lambda: False)

    def unlink(self, missing_ok=False):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError):
        workflow_workspace._delete_workspace_entry(target)
    assert target.exists()
