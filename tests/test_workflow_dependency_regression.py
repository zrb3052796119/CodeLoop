"""Real promotion regressions for dependencies outside a workflow's write set."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

import minicode.workflow_workspace as workspace_module
from minicode.workflow_workspace import (
    WorkflowIsolationError,
    WorkflowWorkspaceTransaction,
)


def _acceptance_passes(workspace: Path) -> bool:
    a = int((workspace / "a.txt").read_text(encoding="utf-8"))
    b = int((workspace / "b.txt").read_text(encoding="utf-8"))
    return a + b <= 1


_PROMOTE_A_IN_OTHER_PROCESS = """
from pathlib import Path
import sys
from minicode.workflow_workspace import WorkflowWorkspaceTransaction

with WorkflowWorkspaceTransaction(
    sys.argv[1], baseline_scope_paths=("a.txt",)
) as transaction:
    (transaction.cwd / "a.txt").write_text("1\\n", encoding="utf-8")
    frozen = transaction.freeze(allowed_paths=("a.txt",))
    with frozen.verification_workspace() as cwd:
        assert int((cwd / "a.txt").read_text()) + int((cwd / "b.txt").read_text()) <= 1
    frozen.promote(approved_patch_digest=frozen.patch_digest)
    assert transaction.parent_promotion_completed
"""


@pytest.mark.parametrize("other_process", [False, True])
def test_disjoint_writes_cannot_promote_over_a_changed_read_dependency(
    tmp_path: Path, other_process: bool
) -> None:
    """Both local checks pass, but the second commit must preserve a+b<=1."""
    (tmp_path / "a.txt").write_text("0\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("0\n", encoding="utf-8")

    with WorkflowWorkspaceTransaction(
        str(tmp_path), baseline_scope_paths=("b.txt",)
    ) as workflow_b:
        (workflow_b.cwd / "b.txt").write_text("1\n", encoding="utf-8")
        frozen_b = workflow_b.freeze(allowed_paths=("b.txt",))
        with frozen_b.verification_workspace() as verification_b:
            assert _acceptance_passes(verification_b)

        if other_process:
            subprocess.run(
                [sys.executable, "-c", _PROMOTE_A_IN_OTHER_PROCESS, str(tmp_path)],
                cwd=Path(workspace_module.__file__).resolve().parents[1],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        else:
            with WorkflowWorkspaceTransaction(
                str(tmp_path), baseline_scope_paths=("a.txt",)
            ) as workflow_a:
                (workflow_a.cwd / "a.txt").write_text("1\n", encoding="utf-8")
                frozen_a = workflow_a.freeze(allowed_paths=("a.txt",))
                with frozen_a.verification_workspace() as verification_a:
                    assert _acceptance_passes(verification_a)
                frozen_a.promote(approved_patch_digest=frozen_a.patch_digest)
                assert workflow_a.parent_promotion_completed

        with pytest.raises(WorkflowIsolationError, match="parent workspace changed"):
            frozen_b.promote(approved_patch_digest=frozen_b.patch_digest)

        assert not workflow_b.parent_promotion_completed
        assert not workflow_b.parent_promotion_unknown
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "0\n"
        assert _acceptance_passes(tmp_path)


def test_no_change_result_cannot_skip_dependency_validation(tmp_path: Path) -> None:
    """An empty patch must not report stale acceptance as a committed result."""
    (tmp_path / "a.txt").write_text("0\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("0\n", encoding="utf-8")
    with WorkflowWorkspaceTransaction(
        str(tmp_path), baseline_scope_paths=("b.txt",)
    ) as transaction:
        frozen = transaction.freeze(allowed_paths=("b.txt",))
        with frozen.verification_workspace() as verification:
            assert (verification / "a.txt").read_text(encoding="utf-8") == "0\n"

        (tmp_path / "a.txt").write_text("1\n", encoding="utf-8")
        with pytest.raises(WorkflowIsolationError, match="parent workspace changed"):
            frozen.promote(approved_patch_digest=frozen.patch_digest)

        assert not transaction.parent_promotion_completed
        assert not transaction.parent_promotion_unknown
