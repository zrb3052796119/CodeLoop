"""V2 scope and digest gates for isolated workflow mutations."""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from minicode import advisory_lock as advisory_lock_module
from minicode import workflow_workspace as workspace_module
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_BYTES,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import (
    SchedulerAuditValidationError,
    _validate_frozen_diff,
)
from minicode.task_deadline import (
    TaskDeadlineExceeded as _TaskDeadlineExceeded,
)
from minicode.tools.task import (
    _WorkflowCommitStateUnknown,
)
from minicode.workflow_workspace import (
    WorkflowIsolationError as _WorkflowIsolationError,
)
from minicode.workflow_workspace import (
    WorkflowWorkspaceTransaction as _WorkflowWorkspaceTransaction,
)


class _TrackedLockHandle:
    def __init__(self, handle) -> None:
        self._handle = handle
        self.close_calls = 0

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self.close_calls += 1
        self._handle.close()

    @property
    def closed(self) -> bool:
        return self._handle.closed


def _track_workflow_lock_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> list[_TrackedLockHandle]:
    real_path_open = Path.open
    tracked_handles: list[_TrackedLockHandle] = []

    def tracked_open(path: Path, *args, **kwargs):
        handle = real_path_open(path, *args, **kwargs)
        if path.suffix != ".lock":
            return handle
        tracked = _TrackedLockHandle(handle)
        tracked_handles.append(tracked)
        return tracked

    monkeypatch.setattr(Path, "open", tracked_open)
    return tracked_handles


def test_change_set_is_scope_checked_and_digest_bound(tmp_path: Path) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")

        change_set = transaction.change_set(allowed_paths=("allowed.txt",))

        assert change_set["baseDigest"].startswith("sha256:")
        assert change_set["patchDigest"].startswith("sha256:")
        assert change_set["files"] == [
            {"path": "allowed.txt", "action": "modified"}
        ]
        transaction.commit(
            expected_patch_digest=change_set["patchDigest"],
            allowed_paths=("allowed.txt",),
        )

    assert target.read_text(encoding="utf-8") == "after"


def test_non_utf8_large_frozen_diff_is_bounded_uncommitted_audit_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_bytes(b"before\n")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_bytes(b"\xff" * 13_000)
        exact_patch = transaction._staged_patch()
        change_set = transaction.change_set(allowed_paths=("allowed.txt",))
        frozen_diff = transaction.frozen_diff_artifact()

    expected_preview = (
        exact_patch[:6_000]
        + b"\n...<truncated>...\n"
        + exact_patch[-6_000:]
    )
    assert frozen_diff["encoding"] == "base64"
    assert frozen_diff["complete"] is False
    assert frozen_diff["byteCount"] == len(exact_patch)
    assert base64.b64decode(frozen_diff["diff"], validate=True) == expected_preview

    authority = object()
    store = SchedulerArtifactStore(scheduler_authority=authority)
    sealed = store.publish(
        "artifact_non_utf8_frozen_diff",
        frozen_diff,
        authority=authority,
    )
    assert len(sealed.content.encode("utf-8")) <= MAX_SCHEDULER_ARTIFACT_BYTES
    offline_document = json.loads(sealed.content)
    _validate_frozen_diff(
        offline_document,
        base_digest=str(change_set["baseDigest"]),
        patch_digest=str(change_set["patchDigest"]),
        require_complete=False,
    )
    with pytest.raises(
        SchedulerAuditValidationError,
        match="committed scheduler frozen diff must be complete",
    ):
        _validate_frozen_diff(
            offline_document,
            base_digest=str(change_set["baseDigest"]),
            patch_digest=str(change_set["patchDigest"]),
            require_complete=True,
        )

    assert target.read_bytes() == b"before\n"


def test_change_set_rejects_a_changed_path_outside_assignment_scope(
    tmp_path: Path,
) -> None:
    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "outside.txt").write_text("escaped", encoding="utf-8")

        with pytest.raises(_WorkflowIsolationError, match="outside allowed paths"):
            transaction.change_set(allowed_paths=("allowed.txt",))

    assert not (tmp_path / "outside.txt").exists()


def test_commit_rejects_changes_made_after_verification(tmp_path: Path) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        isolated = Path(transaction.cwd, "allowed.txt")
        isolated.write_text("verified", encoding="utf-8")
        verified = transaction.change_set(allowed_paths=("allowed.txt",))
        isolated.write_text("changed after verification", encoding="utf-8")

        with pytest.raises(_WorkflowIsolationError, match="patch digest changed"):
            transaction.commit(
                expected_patch_digest=verified["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("git_workspace", [False, True])
def test_commit_rejects_unrelated_parent_baseline_drift(
    tmp_path: Path,
    git_workspace: bool,
) -> None:
    if git_workspace:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "allowed.txt"
    unrelated = tmp_path / "other.txt"
    target.write_text("before", encoding="utf-8")
    unrelated.write_text("baseline", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))
        unrelated.write_text("concurrent update", encoding="utf-8")

        with pytest.raises(_WorkflowIsolationError, match="parent workspace changed"):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"
    assert unrelated.read_text(encoding="utf-8") == "concurrent update"


def test_snapshot_never_runs_repository_configured_git_filters(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    marker = tmp_path.parent / f"{tmp_path.name}-filter-ran"
    filter_program = tmp_path / ".git" / "hostile-clean-filter"
    filter_program.write_text(
        "#!/bin/sh\n"
        f"touch {shlex.quote(str(marker))}\n"
        "cat\n",
        encoding="utf-8",
    )
    filter_program.chmod(0o755)
    subprocess.run(
        [
            "git",
            "config",
            "filter.hostile.clean",
            shlex.quote(str(filter_program)),
        ],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / ".gitattributes").write_text(
        "*.txt filter=hostile\n",
        encoding="utf-8",
    )
    (tmp_path / "payload.txt").write_text("payload\n", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        assert (transaction.cwd / "payload.txt").read_text(encoding="utf-8") == (
            "payload\n"
        )
        (transaction.cwd / "payload.txt").write_text(
            "updated\n",
            encoding="utf-8",
        )
        change_set = transaction.change_set(allowed_paths=("payload.txt",))
        transaction.commit(
            expected_patch_digest=change_set["patchDigest"],
            allowed_paths=("payload.txt",),
        )

    assert not marker.exists()
    assert (tmp_path / "payload.txt").read_text(encoding="utf-8") == "updated\n"


def test_scheduler_git_environment_does_not_inherit_host_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-git")
    monkeypatch.setenv("CUSTOM_TOKEN", "must-not-reach-git")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        environment = transaction._git_environment

        assert "OPENAI_API_KEY" not in environment
        assert "CUSTOM_TOKEN" not in environment
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["PATH"] == os.defpath
        for name in ("TMP", "TEMP", "TMPDIR"):
            assert Path(environment[name]).is_relative_to(transaction._temp_root)


def test_verifier_side_effects_are_discarded_before_exact_patch_commit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))

        with transaction.verification_workspace() as verification_cwd:
            Path(verification_cwd, ".pytest_cache").mkdir()
            Path(verification_cwd, ".pytest_cache", "README.md").write_text(
                "generated by verifier",
                encoding="utf-8",
            )

        transaction.commit(
            expected_patch_digest=frozen["patchDigest"],
            allowed_paths=("allowed.txt",),
        )

    assert target.read_text(encoding="utf-8") == "after"
    assert not (tmp_path / ".pytest_cache").exists()


def test_verification_clone_physically_excludes_forbidden_data(
    tmp_path: Path,
) -> None:
    (tmp_path / "allowed.txt").write_text("before", encoding="utf-8")
    (tmp_path / ".env.local").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / "credentials.json").write_text(
        '{"token":"secret"}',
        encoding="utf-8",
    )

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        transaction.change_set(allowed_paths=("allowed.txt",))

        with transaction.verification_workspace(
            forbidden_paths=(".env", "credentials.json", ".git"),
        ) as verification_cwd:
            assert (verification_cwd / "allowed.txt").is_file()
            assert not (verification_cwd / ".env.local").exists()
            assert not (verification_cwd / "credentials.json").exists()
            assert not (verification_cwd / ".git").exists()


def test_nested_verification_scope_is_relative_to_invocation_cwd(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "visible.txt").write_text("visible", encoding="utf-8")
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(nested)) as transaction:
        Path(transaction.cwd, "visible.txt").write_text("changed", encoding="utf-8")
        transaction.change_set(
            allowed_paths=("visible.txt",),
            paths_relative_to_cwd=True,
        )

        with transaction.verification_workspace(
            read_roots=("visible.txt", "secret.txt"),
            forbidden_paths=("secret.txt",),
            paths_relative_to_cwd=True,
        ) as verification_cwd:
            assert (verification_cwd / "visible.txt").is_file()
            assert not (verification_cwd / "secret.txt").exists()
            assert not (verification_cwd.parent / "outside.txt").exists()


def test_verification_clone_physically_prunes_outside_read_roots(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    private = tmp_path / "private"
    allowed.mkdir()
    private.mkdir()
    (allowed / "test_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (private / "token.txt").write_text("secret", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed", "test_target.py").write_text(
            "VALUE = 2\n",
            encoding="utf-8",
        )
        transaction.change_set(allowed_paths=("allowed",))

        with transaction.verification_workspace(
            read_roots=("allowed",),
        ) as verification_cwd:
            assert (verification_cwd / "allowed" / "test_target.py").is_file()
            assert not (verification_cwd / "private").exists()


def test_post_execution_escaping_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "escape").symlink_to(outside)

        with pytest.raises(_WorkflowIsolationError, match="escaping symlink"):
            transaction.change_set(allowed_paths=("escape",))

    assert outside.read_text(encoding="utf-8") == "outside"


def test_structured_scope_paths_are_relative_to_invocation_cwd(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    nested = tmp_path / "sub"
    nested.mkdir()
    target = nested / "file.txt"
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(nested)) as transaction:
        Path(transaction.cwd, "file.txt").write_text("after", encoding="utf-8")
        change_set = transaction.change_set(
            allowed_paths=("file.txt",),
            paths_relative_to_cwd=True,
        )

        assert change_set["files"] == [
            {"path": "file.txt", "action": "modified"}
        ]
        transaction.commit(
            expected_patch_digest=change_set["patchDigest"],
            allowed_paths=("file.txt",),
            paths_relative_to_cwd=True,
        )

    assert target.read_text(encoding="utf-8") == "after"


def test_expired_commit_guard_prevents_parent_promotion(tmp_path: Path) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))
        transaction.set_operation_guard(
            deadline_monotonic=time.monotonic() - 1,
            timeout_seconds=1,
            operation_checkpoint=None,
        )

        with pytest.raises(_TaskDeadlineExceeded):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"


def test_parent_promotion_lock_key_follows_filesystem_aliases(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    alias = tmp_path / "repository-alias"
    try:
        alias.symlink_to(source_root, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    assert workspace_module._workflow_parent_lock_key(
        source_root
    ) == workspace_module._workflow_parent_lock_key(alias)


def test_parent_promotion_lock_key_fails_closed_when_identity_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "repository"
    source_root.mkdir()
    real_stat = workspace_module.os.stat

    def unavailable(path, *args, **kwargs):
        if Path(path) == source_root:
            raise OSError("filesystem identity unavailable")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(workspace_module.os, "stat", unavailable)

    with pytest.raises(
        _WorkflowIsolationError,
        match="workflow commit lock identity is unavailable",
    ):
        workspace_module._workflow_parent_lock_key(source_root)


def test_parent_promotion_lock_namespace_does_not_follow_tmpdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace_before = workspace_module._workflow_parent_lock_namespace()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "different-tmp"))
    monkeypatch.setattr(
        workspace_module.tempfile,
        "gettempdir",
        lambda: str(tmp_path / "different-tmp"),
    )

    assert workspace_module._workflow_parent_lock_namespace() == namespace_before


def test_windows_parent_promotion_lock_namespace_requires_local_app_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_module,
        "_workflow_lock_platform_name",
        lambda: "nt",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert workspace_module._workflow_parent_lock_namespace() == (
        tmp_path / "MiniCode" / "workflow-locks"
    )

    monkeypatch.delenv("LOCALAPPDATA")
    with pytest.raises(
        _WorkflowIsolationError,
        match="workflow commit lock user namespace is unavailable",
    ):
        workspace_module._workflow_parent_lock_namespace()


def test_commit_fails_closed_for_symlinked_lock_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    actual_namespace = tmp_path / "actual-locks"
    actual_namespace.mkdir(mode=0o700)
    namespace_alias = tmp_path / "lock-namespace"
    try:
        namespace_alias.symlink_to(actual_namespace, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    monkeypatch.setattr(
        workspace_module,
        "_workflow_parent_lock_namespace",
        lambda: namespace_alias,
    )

    with _WorkflowWorkspaceTransaction(str(workspace)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))

        with pytest.raises(
            _WorkflowIsolationError,
            match="workflow commit lock namespace is unavailable",
        ):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"


def test_commit_fails_closed_for_lock_namespace_with_wrong_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "geteuid"):
        pytest.skip("POSIX owner validation")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    lock_namespace = tmp_path / "lock-namespace"
    lock_namespace.mkdir(mode=0o700)
    actual_user_id = os.geteuid()
    monkeypatch.setattr(
        workspace_module,
        "_workflow_parent_lock_namespace",
        lambda: lock_namespace,
    )
    monkeypatch.setattr(
        workspace_module.os,
        "geteuid",
        lambda: actual_user_id + 1,
    )

    with _WorkflowWorkspaceTransaction(str(workspace)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))

        with pytest.raises(
            _WorkflowIsolationError,
            match="workflow commit lock namespace is unavailable: wrong owner",
        ):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"


def test_commit_fails_closed_when_advisory_lock_backend_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    tracked_handles = _track_workflow_lock_handles(monkeypatch)

    monkeypatch.setattr(
        advisory_lock_module,
        "_platform_name",
        lambda: "nt",
    )
    monkeypatch.setattr(advisory_lock_module, "msvcrt", None)

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))

        with pytest.raises(
            _WorkflowIsolationError,
            match="workflow commit lock is unavailable",
        ):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1


@pytest.mark.parametrize("failure_site", ["chmod", "prepare"])
def test_parent_promotion_closes_lock_handle_when_setup_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    tracked_handles = _track_workflow_lock_handles(monkeypatch)
    releases = 0
    interruption = KeyboardInterrupt(f"lock {failure_site} interrupted")
    real_chmod = workspace_module.os.chmod

    def interrupted_prepare(_file_descriptor: int, *, mode: int = 0o600) -> None:
        del mode
        raise interruption

    def interrupted_chmod(path, mode: int) -> None:
        if Path(path).suffix == ".lock":
            raise interruption
        real_chmod(path, mode)

    def released(_file_descriptor: int) -> None:
        nonlocal releases
        releases += 1

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))
        if failure_site == "prepare":
            monkeypatch.setattr(
                advisory_lock_module,
                "prepare_advisory_lock_file",
                interrupted_prepare,
            )
        else:
            monkeypatch.setattr(
                workspace_module.os,
                "chmod",
                interrupted_chmod,
            )
        monkeypatch.setattr(
            advisory_lock_module,
            "release_advisory_lock",
            released,
        )

        with pytest.raises(KeyboardInterrupt) as raised:
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert raised.value is interruption
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1
    assert releases == 0
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize(
    "error_type",
    [OSError, RuntimeError, KeyboardInterrupt],
)
def test_parent_promotion_closes_lock_handle_when_release_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    tracked_handles = _track_workflow_lock_handles(monkeypatch)
    release_calls = 0
    release_error = error_type("lock release failed")

    def acquired(_file_descriptor: int) -> bool:
        return True

    def interrupted_release(_file_descriptor: int) -> None:
        nonlocal release_calls
        release_calls += 1
        raise release_error

    monkeypatch.setattr(
        advisory_lock_module,
        "acquire_advisory_lock",
        acquired,
    )
    monkeypatch.setattr(
        advisory_lock_module,
        "release_advisory_lock",
        interrupted_release,
    )

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        with pytest.raises(error_type) as raised:
            with transaction._cross_process_commit_lock():
                pass

    assert raised.value is release_error
    assert release_calls == 1
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("cleanup_site", ["release", "close"])
def test_parent_promotion_cleanup_cannot_replace_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_site: str,
) -> None:
    tracked_handles = _track_workflow_lock_handles(monkeypatch)
    primary_error = _WorkflowCommitStateUnknown("parent state is unknown")
    cleanup_error = RuntimeError(f"lock {cleanup_site} failed")
    release_calls = 0

    def acquired(_file_descriptor: int) -> bool:
        return True

    def released(_file_descriptor: int) -> None:
        nonlocal release_calls
        release_calls += 1
        if cleanup_site == "release":
            raise cleanup_error

    real_close = _TrackedLockHandle.close

    def close_then_maybe_fail(handle: _TrackedLockHandle) -> None:
        real_close(handle)
        if cleanup_site == "close":
            raise cleanup_error

    monkeypatch.setattr(
        advisory_lock_module,
        "acquire_advisory_lock",
        acquired,
    )
    monkeypatch.setattr(
        advisory_lock_module,
        "release_advisory_lock",
        released,
    )
    monkeypatch.setattr(_TrackedLockHandle, "close", close_then_maybe_fail)

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        with pytest.raises(_WorkflowCommitStateUnknown) as raised:
            with transaction._cross_process_commit_lock():
                raise primary_error

    assert raised.value is primary_error
    assert release_calls == 1
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1


def test_parent_promotion_propagates_close_failure_without_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked_handles = _track_workflow_lock_handles(monkeypatch)
    close_error = RuntimeError("lock close failed")
    release_calls = 0

    def acquired(_file_descriptor: int) -> bool:
        return True

    def released(_file_descriptor: int) -> None:
        nonlocal release_calls
        release_calls += 1

    real_close = _TrackedLockHandle.close

    def close_then_fail(handle: _TrackedLockHandle) -> None:
        real_close(handle)
        raise close_error

    monkeypatch.setattr(
        advisory_lock_module,
        "acquire_advisory_lock",
        acquired,
    )
    monkeypatch.setattr(
        advisory_lock_module,
        "release_advisory_lock",
        released,
    )
    monkeypatch.setattr(_TrackedLockHandle, "close", close_then_fail)

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        with pytest.raises(RuntimeError) as raised:
            with transaction._cross_process_commit_lock():
                pass

    assert raised.value is close_error
    assert release_calls == 1
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1


def test_advisory_lock_contention_cannot_outlive_commit_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    tracked_handles = _track_workflow_lock_handles(monkeypatch)
    attempts = 0
    releases = 0
    clock = 100.0

    def contended(_file_descriptor: int) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    def released(_file_descriptor: int) -> None:
        nonlocal releases
        releases += 1

    def monotonic() -> float:
        return clock

    def wait(seconds: float) -> None:
        nonlocal clock
        clock += seconds

    monkeypatch.setattr(
        advisory_lock_module,
        "acquire_advisory_lock",
        contended,
    )
    monkeypatch.setattr(
        advisory_lock_module,
        "release_advisory_lock",
        released,
    )
    monkeypatch.setattr(workspace_module.time, "monotonic", monotonic)
    monkeypatch.setattr(workspace_module.time, "sleep", wait)

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))
        transaction.set_operation_guard(
            deadline_monotonic=clock + 0.02,
            timeout_seconds=0.02,
            operation_checkpoint=None,
        )

        with pytest.raises(_TaskDeadlineExceeded):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert attempts >= 1
    assert releases == 0
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1
    assert target.read_text(encoding="utf-8") == "before"


def test_successful_parent_promotion_releases_advisory_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    tracked_handles = _track_workflow_lock_handles(monkeypatch)
    events: list[tuple[str, int]] = []

    def prepared(file_descriptor: int, *, mode: int = 0o600) -> None:
        assert mode == 0o600
        events.append(("prepare", file_descriptor))

    attempts = iter((False, True))

    def acquired(file_descriptor: int) -> bool:
        events.append(("acquire", file_descriptor))
        return next(attempts)

    def released(file_descriptor: int) -> None:
        events.append(("release", file_descriptor))

    monkeypatch.setattr(
        advisory_lock_module,
        "prepare_advisory_lock_file",
        prepared,
    )
    monkeypatch.setattr(
        advisory_lock_module,
        "acquire_advisory_lock",
        acquired,
    )
    monkeypatch.setattr(
        advisory_lock_module,
        "release_advisory_lock",
        released,
    )

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text(
            "after",
            encoding="utf-8",
        )
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))
        transaction.commit(
            expected_patch_digest=frozen["patchDigest"],
            allowed_paths=("allowed.txt",),
        )

    assert [event for event, _descriptor in events] == [
        "prepare",
        "acquire",
        "acquire",
        "release",
    ]
    assert len({descriptor for _event, descriptor in events}) == 1
    assert len(tracked_handles) == 1
    assert tracked_handles[0].closed is True
    assert tracked_handles[0].close_calls == 1
    assert target.read_text(encoding="utf-8") == "after"


def test_commit_guard_propagates_cancellation_before_parent_write(
    tmp_path: Path,
) -> None:
    class Cancelled(RuntimeError):
        pass

    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))
        transaction.set_operation_guard(
            deadline_monotonic=None,
            timeout_seconds=1,
            operation_checkpoint=lambda: (_ for _ in ()).throw(Cancelled()),
        )

        with pytest.raises(Cancelled):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

    assert target.read_text(encoding="utf-8") == "before"


def test_interrupted_apply_ignores_empty_directories_not_carried_by_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    original_run_checked = _WorkflowWorkspaceTransaction._run_checked

    def apply_then_raise(self, args, **kwargs):
        completed = original_run_checked(self, args, **kwargs)
        if (
            "apply" in args
            and "--check" not in args
            and kwargs.get("use_operation_guard") is False
        ):
            raise RuntimeError("post-apply observer failed")
        return completed

    monkeypatch.setattr(
        _WorkflowWorkspaceTransaction,
        "_run_checked",
        apply_then_raise,
    )

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        Path(transaction.cwd, "empty").mkdir()
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))

        with pytest.raises(RuntimeError, match="post-apply observer failed"):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

        assert transaction.parent_promotion_completed is True
        assert transaction.parent_promotion_unknown is False

    assert target.read_text(encoding="utf-8") == "after"
    assert not (tmp_path / "empty").exists()


def test_second_interrupt_during_apply_reconciliation_preserves_unknown_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "allowed.txt"
    target.write_text("before", encoding="utf-8")
    original_run_checked = _WorkflowWorkspaceTransaction._run_checked
    original_promotion_digest = (
        _WorkflowWorkspaceTransaction._promotion_state_digest
    )

    def apply_then_raise(self, args, **kwargs):
        completed = original_run_checked(self, args, **kwargs)
        if (
            "apply" in args
            and "--check" not in args
            and kwargs.get("use_operation_guard") is False
        ):
            raise RuntimeError("post-apply observer failed")
        return completed

    def interrupt_reconciliation(self, root, *, use_operation_guard=True):
        if root == self.source_root and use_operation_guard is False:
            raise KeyboardInterrupt("reconciliation interrupted")
        return original_promotion_digest(
            self,
            root,
            use_operation_guard=use_operation_guard,
        )

    monkeypatch.setattr(
        _WorkflowWorkspaceTransaction,
        "_run_checked",
        apply_then_raise,
    )
    monkeypatch.setattr(
        _WorkflowWorkspaceTransaction,
        "_promotion_state_digest",
        interrupt_reconciliation,
    )

    with _WorkflowWorkspaceTransaction(str(tmp_path)) as transaction:
        Path(transaction.cwd, "allowed.txt").write_text("after", encoding="utf-8")
        frozen = transaction.change_set(allowed_paths=("allowed.txt",))

        with pytest.raises(KeyboardInterrupt, match="reconciliation interrupted"):
            transaction.commit(
                expected_patch_digest=frozen["patchDigest"],
                allowed_paths=("allowed.txt",),
            )

        assert transaction.parent_promotion_completed is False
        assert transaction.parent_promotion_unknown is True

    assert target.read_text(encoding="utf-8") == "after"
