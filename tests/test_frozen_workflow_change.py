"""Public state transition for frozen workflow changes."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import minicode.workflow_workspace as workspace_module
from minicode.workflow_workspace import (
    FrozenWorkflowChange,
    WorkflowIsolationError,
    WorkflowWorkspaceTransaction,
)


def test_frozen_capability_cannot_be_constructed_directly() -> None:
    with pytest.raises(
        TypeError,
        match="created by WorkflowWorkspaceTransaction.freeze",
    ):
        FrozenWorkflowChange()


def test_freeze_returns_detached_documents_and_owns_promotion(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )

        frozen = transaction.freeze(allowed_paths=("allowed.txt",))

        assert isinstance(frozen, FrozenWorkflowChange)
        assert not hasattr(transaction, "change_set")
        assert not hasattr(transaction, "frozen_diff_artifact")
        assert not hasattr(transaction, "verification_workspace")
        assert not hasattr(transaction, "commit")

        change_set = frozen.change_set_document
        assert change_set == {
            "workspaceRef": "isolated://workflow",
            "baseDigest": transaction.base_digest,
            "patchDigest": frozen.patch_digest,
            "files": [{"path": "allowed.txt", "action": "modified"}],
        }
        change_set["files"].clear()
        assert frozen.change_set_document["files"] == [
            {"path": "allowed.txt", "action": "modified"}
        ]
        assert frozen.diff_artifact_document["patchDigest"] == frozen.patch_digest

        frozen.promote(approved_patch_digest=frozen.patch_digest)

    assert target.read_text(encoding="utf-8") == "after"


def test_freeze_is_a_single_state_transition(tmp_path: Path) -> None:
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        frozen = transaction.freeze(allowed_paths=(".",))

        with pytest.raises(
            WorkflowIsolationError,
            match="workflow change set was already frozen",
        ):
            transaction.freeze(allowed_paths=(".",))

        assert frozen.change_set_document["files"] == []


def test_failed_freeze_can_retry_without_reusing_a_partial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        original_copy = WorkflowWorkspaceTransaction._copytree_with_checkpoints
        failed_once = False

        def copy_with_one_partial_failure(self, source, destination, **kwargs):
            nonlocal failed_once
            if not failed_once and destination.parent.name.startswith(
                "frozen-workspace-"
            ):
                failed_once = True
                destination.mkdir()
                raise WorkflowIsolationError("injected frozen snapshot failure")
            return original_copy(self, source, destination, **kwargs)

        monkeypatch.setattr(
            WorkflowWorkspaceTransaction,
            "_copytree_with_checkpoints",
            copy_with_one_partial_failure,
        )

        with pytest.raises(
            WorkflowIsolationError,
            match="injected frozen snapshot failure",
        ):
            transaction.freeze(allowed_paths=(".",))

        frozen = transaction.freeze(allowed_paths=(".",))
        assert frozen.change_set_document["files"] == []


def test_freeze_maps_snapshot_os_errors_and_remains_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        original_mkdtemp = workspace_module.tempfile.mkdtemp
        failed_once = False

        def mkdtemp_with_one_failure(*args, **kwargs):
            nonlocal failed_once
            if kwargs.get("prefix") == "frozen-workspace-" and not failed_once:
                failed_once = True
                raise OSError("injected snapshot allocation failure")
            return original_mkdtemp(*args, **kwargs)

        monkeypatch.setattr(
            workspace_module.tempfile,
            "mkdtemp",
            mkdtemp_with_one_failure,
        )

        with pytest.raises(
            WorkflowIsolationError,
            match="workflow frozen snapshot could not be created",
        ):
            transaction.freeze(allowed_paths=(".",))

        frozen = transaction.freeze(allowed_paths=(".",))
        assert frozen.change_set_document["files"] == []


def test_freeze_rejects_a_tampered_trusted_git_template(tmp_path: Path) -> None:
    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction._trusted_git_root, "config").write_text(
            "[core]\nrepositoryformatversion = 0\nwhitespace = -trailing-space\n",
            encoding="utf-8",
        )

        with pytest.raises(
            WorkflowIsolationError,
            match="trusted Git metadata changed after initialization",
        ):
            transaction.freeze(allowed_paths=(".",))


def test_verification_uses_frozen_bytes_and_promotion_rejects_later_drift(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        isolated = Path(transaction.cwd, "allowed.txt")
        isolated.write_text("frozen", encoding="utf-8")
        frozen = transaction.freeze(allowed_paths=("allowed.txt",))
        isolated.write_text("drifted", encoding="utf-8")

        with frozen.verification_workspace() as verification_cwd:
            assert Path(verification_cwd, "allowed.txt").read_text(
                encoding="utf-8"
            ) == "frozen"

        with pytest.raises(
            WorkflowIsolationError,
            match="workflow patch digest changed after verification",
        ):
            frozen.promote(approved_patch_digest=frozen.patch_digest)

    assert target.read_text(encoding="utf-8") == "before"


def test_verification_rebuilds_a_tampered_frozen_git_index(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.freeze(allowed_paths=("allowed.txt",))

        transaction._run_checked(
            transaction._git_args("reset", "-q", transaction._baseline, "--"),
            cwd=frozen._frozen_workspace_root,
            env=transaction._git_environment,
        )

        with frozen.verification_workspace(
            retain_git_metadata=True,
        ) as verification_cwd:
            patch = transaction._run_checked(
                transaction._git_args(
                    "diff",
                    "--cached",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    transaction._baseline,
                    "--",
                ),
                cwd=verification_cwd,
                env=transaction._git_environment,
            ).stdout

        assert "sha256:" + hashlib.sha256(patch).hexdigest() == frozen.patch_digest


@pytest.mark.parametrize("metadata_attack", ["config", "info_attributes"])
def test_verification_rebuilds_trusted_git_control_metadata(
    tmp_path: Path,
    metadata_attack: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before\n", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after with trailing whitespace \n",
            encoding="utf-8",
        )
        if metadata_attack == "config":
            transaction._run_checked(
                transaction._git_args(
                    "config",
                    "core.whitespace",
                    "-trailing-space",
                ),
                cwd=transaction.workspace_root,
                env=transaction._git_environment,
            )
        else:
            Path(
                transaction.workspace_root,
                ".git",
                "info",
                "attributes",
            ).write_text("* -whitespace\n", encoding="utf-8")

        frozen = transaction.freeze(allowed_paths=("allowed.txt",))
        if metadata_attack == "config":
            transaction._run_checked(
                transaction._git_args(
                    "config",
                    "core.whitespace",
                    "-trailing-space",
                ),
                cwd=frozen._frozen_workspace_root,
                env=transaction._git_environment,
            )
        else:
            Path(
                frozen._frozen_workspace_root,
                ".git",
                "info",
                "attributes",
            ).write_text("* -whitespace\n", encoding="utf-8")

        with frozen.verification_workspace(
            retain_git_metadata=True,
        ) as verification_cwd:
            oracle = transaction._run_checked(
                transaction._git_args("diff", "--cached", "--check"),
                cwd=verification_cwd,
                env=transaction._git_environment,
                check=False,
            )

        assert oracle.returncode != 0
        assert b"trailing whitespace" in oracle.stdout


def test_promotion_requires_the_approved_patch_digest(tmp_path: Path) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.freeze(allowed_paths=("allowed.txt",))

        with pytest.raises(
            WorkflowIsolationError,
            match="workflow patch digest changed after verification",
        ):
            frozen.promote(approved_patch_digest="sha256:" + "0" * 64)

    assert target.read_text(encoding="utf-8") == "before"


def test_promotion_ignores_worker_git_config_and_checks_target_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        link = Path(transaction.cwd, "link.txt")
        try:
            link.symlink_to("target.txt")
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")
        transaction._run_checked(
            transaction._git_args("config", "core.symlinks", "false"),
            cwd=transaction.workspace_root,
            env=transaction._git_environment,
        )
        frozen = transaction.freeze(allowed_paths=("link.txt",))

        frozen.promote(approved_patch_digest=frozen.patch_digest)

        assert transaction.parent_promotion_completed is True
        assert transaction.parent_promotion_unknown is False

    promoted = tmp_path / "link.txt"
    assert promoted.is_symlink()
    assert promoted.readlink() == Path("target.txt")


def test_promotion_cannot_report_success_for_a_wrong_parent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    original_run_checked = WorkflowWorkspaceTransaction._run_checked

    def apply_then_corrupt_parent(self, args, **kwargs):
        completed = original_run_checked(self, args, **kwargs)
        if (
            "apply" in args
            and "--check" not in args
            and kwargs.get("use_operation_guard") is False
        ):
            target.write_text("wrong", encoding="utf-8")
        return completed

    monkeypatch.setattr(
        WorkflowWorkspaceTransaction,
        "_run_checked",
        apply_then_corrupt_parent,
    )

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.freeze(allowed_paths=("allowed.txt",))

        with pytest.raises(
            WorkflowIsolationError,
            match="workflow parent state differs after promotion",
        ):
            frozen.promote(approved_patch_digest=frozen.patch_digest)

        assert transaction.parent_promotion_completed is False
        assert transaction.parent_promotion_unknown is True

    assert target.read_text(encoding="utf-8") == "wrong"


def test_promotion_maps_metadata_allocation_errors_and_remains_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.freeze(allowed_paths=("allowed.txt",))
        original_temporary_directory = workspace_module.tempfile.TemporaryDirectory
        failed_once = False

        def temporary_directory_with_one_failure(*args, **kwargs):
            nonlocal failed_once
            if kwargs.get("prefix") == "promotion-git-" and not failed_once:
                failed_once = True
                raise OSError("injected promotion metadata failure")
            return original_temporary_directory(*args, **kwargs)

        monkeypatch.setattr(
            workspace_module.tempfile,
            "TemporaryDirectory",
            temporary_directory_with_one_failure,
        )

        with pytest.raises(
            WorkflowIsolationError,
            match="workflow promotion metadata could not be created",
        ):
            frozen.promote(approved_patch_digest=frozen.patch_digest)

        assert transaction.parent_promotion_completed is False
        assert transaction.parent_promotion_unknown is False
        assert target.read_text(encoding="utf-8") == "before"

        frozen.promote(approved_patch_digest=frozen.patch_digest)

    assert target.read_text(encoding="utf-8") == "after"


def test_frozen_capability_fails_closed_after_owner_close(tmp_path: Path) -> None:
    transaction = WorkflowWorkspaceTransaction(str(tmp_path))
    frozen = transaction.freeze(allowed_paths=(".",))
    approved_digest = frozen.patch_digest
    transaction.close()

    with pytest.raises(
        WorkflowIsolationError,
        match="workflow frozen change owner is unavailable",
    ):
        _ = frozen.change_set_document
    with pytest.raises(
        WorkflowIsolationError,
        match="workflow frozen change owner is unavailable",
    ):
        _ = frozen.diff_artifact_document
    with pytest.raises(
        WorkflowIsolationError,
        match="workflow frozen change owner is unavailable",
    ):
        with frozen.verification_workspace():
            pass
    with pytest.raises(
        WorkflowIsolationError,
        match="workflow frozen change owner is unavailable",
    ):
        frozen.promote(approved_patch_digest=approved_digest)
