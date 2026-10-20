"""Isolated workspace transaction for centrally scheduled workflows."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Iterator

from minicode import advisory_lock
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
    SchedulerArtifactError,
    _canonical_json,
)
from minicode.task_deadline import (
    DEFAULT_TASK_TIMEOUT_SECONDS,
    TaskDeadlineExceeded,
)
from minicode.workspace_digest_cache import shared_workspace_digest_cache


class WorkflowIsolationError(RuntimeError):
    """The workflow snapshot could not be created or committed safely."""


def _host_is_windows() -> bool:
    return os.name == "nt"


def _delete_workspace_entry(path: Path, *, directory: bool = False) -> None:
    """Remove one file or empty directory from an isolated workspace.

    Git writes object files read-only. Windows refuses ``unlink`` until that
    bit is cleared; other hosts already remove them.
    """

    def remove() -> None:
        if directory:
            path.rmdir()
        else:
            path.unlink()

    try:
        remove()
    except PermissionError:
        if not _host_is_windows():
            raise
        os.chmod(path, stat.S_IWRITE)
        remove()


def trusted_git_search_directories(
    *,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    include_windows_program_files: bool | None = None,
) -> list[str]:
    """Directories allowed to supply the host Git binary.

    ``os.defpath`` stays the trusted default so a workspace-poisoned ``PATH``
    cannot choose Git. Windows images install Git under Program Files, which
    is outside ``os.defpath``, so those host directories are included too.
    Anything inside the workflow source root is dropped.
    """

    env = os.environ if environ is None else environ
    if include_windows_program_files is None:
        include_windows_program_files = os.name == "nt"
    entries: list[str] = []
    seen: set[str] = set()

    def add(entry: str) -> None:
        if not entry or entry in seen:
            return
        path = Path(entry)
        if not path.is_absolute():
            return
        if source_root is not None:
            try:
                if path.resolve(strict=False).is_relative_to(source_root.resolve(strict=False)):
                    return
            except OSError:
                return
        seen.add(entry)
        entries.append(entry)

    for entry in os.defpath.split(os.pathsep):
        add(entry)
    if include_windows_program_files:
        for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            root = env.get(key)
            if root:
                add(str(Path(root) / "Git" / "cmd"))
    return entries


def _workflow_parent_lock_key(source_root: Path) -> str:
    """Bind one promotion lock to the underlying source-root directory."""

    try:
        metadata = os.stat(source_root, follow_symlinks=True)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkflowIsolationError(
            f"workflow commit lock identity is unavailable: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise WorkflowIsolationError(
            "workflow commit lock identity is not a directory"
        )
    device = metadata.st_dev
    inode = metadata.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device < 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        raise WorkflowIsolationError(
            "workflow commit lock identity is unavailable"
        )
    identity = f"workflow-parent-v1:{device}:{inode}".encode("ascii")
    return hashlib.sha256(identity).hexdigest()


def _workflow_lock_platform_name() -> str:
    return os.name


def _workflow_parent_lock_namespace() -> Path:
    """Return a stable per-user namespace independent of process temp config."""

    platform = _workflow_lock_platform_name()
    if platform == "posix":
        get_effective_user = getattr(os, "geteuid", None)
        if get_effective_user is None:
            raise WorkflowIsolationError(
                "workflow commit lock user identity is unavailable"
            )
        user_id = get_effective_user()
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 0:
            raise WorkflowIsolationError(
                "workflow commit lock user identity is unavailable"
            )
        return Path("/tmp") / f"minicode-workflow-locks-{user_id}"
    if platform == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if (
            not isinstance(local_app_data, str)
            or not local_app_data
            or local_app_data != local_app_data.strip()
        ):
            raise WorkflowIsolationError(
                "workflow commit lock user namespace is unavailable"
            )
        root = Path(local_app_data)
        if not root.is_absolute():
            raise WorkflowIsolationError(
                "workflow commit lock user namespace is unavailable"
            )
        return root / "MiniCode" / "workflow-locks"
    raise WorkflowIsolationError(
        "workflow commit lock platform is unsupported"
    )


def _prepare_workflow_parent_lock_namespace(lock_root: Path) -> None:
    """Create and validate the per-user lock namespace without following links."""

    platform = _workflow_lock_platform_name()
    try:
        if platform == "posix":
            try:
                os.mkdir(lock_root, mode=0o700)
            except FileExistsError:
                pass
        elif platform == "nt":
            lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            raise WorkflowIsolationError(
                "workflow commit lock platform is unsupported"
            )
        metadata = os.lstat(lock_root)
    except WorkflowIsolationError:
        raise
    except OSError as exc:
        raise WorkflowIsolationError(
            f"workflow commit lock namespace is unavailable: {exc}"
        ) from exc

    is_link = stat.S_ISLNK(metadata.st_mode)
    if platform == "nt":
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        file_attributes = getattr(metadata, "st_file_attributes", 0)
        is_link = is_link or bool(file_attributes & reparse_flag)
    if is_link or not stat.S_ISDIR(metadata.st_mode):
        raise WorkflowIsolationError(
            "workflow commit lock namespace is unavailable: unsafe path type"
        )
    if platform == "posix":
        get_effective_user = getattr(os, "geteuid", None)
        owner = getattr(metadata, "st_uid", None)
        if get_effective_user is None or owner != get_effective_user():
            raise WorkflowIsolationError(
                "workflow commit lock namespace is unavailable: wrong owner"
            )
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise WorkflowIsolationError(
                "workflow commit lock namespace is unavailable: unsafe mode"
            )


_WORKFLOW_PARENT_COMMIT_LOCK = threading.RLock()
_WORKSPACE_IGNORED_ENTRY_NAMES = frozenset({".git"})
_MUTABLE_GIT_ROOT_ENTRIES = frozenset({"index", "index.lock", "objects"})


class WorkflowWorkspaceTransaction:
    """Run mutable workflow work in a disposable snapshot until freeze.

    Every workspace is snapshotted as plain filesystem data without consulting
    the source repository's index or configuration.  This preserves the
    user's staged, unstaged, ignored and untracked files while ensuring a
    repository-owned clean/process filter or fsmonitor helper cannot execute
    on the host during snapshotting.  Git indexing happens only in the
    scheduler-created standalone repository.  An approved delta is applied to
    the parent only after ``git apply --check``.

    A declared write scope bounds every patch-touched path. It does not prove
    which files influenced the change or its verification, so promotion still
    re-validates the complete parent snapshot under the cross-process lock.
    """

    def __init__(
        self,
        cwd: str,
        *,
        deadline_monotonic: float | None = None,
        timeout_seconds: float | None = DEFAULT_TASK_TIMEOUT_SECONDS,
        operation_checkpoint: object | None = None,
        baseline_scope_paths: tuple[str, ...] | None = None,
        baseline_scope_relative_to_cwd: bool = False,
    ) -> None:
        self._deadline_monotonic: float | None = None
        self._timeout_seconds: float | None = DEFAULT_TASK_TIMEOUT_SECONDS
        self._operation_checkpoint: object | None = None
        self.set_operation_guard(
            deadline_monotonic=deadline_monotonic,
            timeout_seconds=timeout_seconds,
            operation_checkpoint=operation_checkpoint,
        )
        self._commit_checkpoint()
        self.source_cwd = Path(cwd).resolve(strict=True)
        if not self.source_cwd.is_dir():
            raise WorkflowIsolationError("workflow cwd is not a directory")
        self._temporary = tempfile.TemporaryDirectory(prefix="minicode-workflow-")
        self._temp_root = Path(self._temporary.name)
        self._commit_checkpoint()
        self._transaction_lock = threading.RLock()
        self._frozen_change: FrozenWorkflowChange | None = None
        self._parent_promotion_completed = False
        self._parent_promotion_unknown = False
        self.source_root = self._discover_git_root() or self.source_cwd
        self._git_executable = self._resolve_host_git_executable()
        git_home = self._temp_root / "git-home"
        git_home.mkdir()
        hooks_directory = git_home / "hooks"
        hooks_directory.mkdir()
        git_temp = git_home / "tmp"
        git_temp.mkdir()
        self._git_environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"LANG", "LC_ALL", "LC_CTYPE", "PATHEXT", "SYSTEMROOT", "WINDIR"}
            and isinstance(value, str)
        }
        self._git_environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": str(git_home),
                "TMP": str(git_temp),
                "TEMP": str(git_temp),
                "TMPDIR": str(git_temp),
                "XDG_CONFIG_HOME": str(git_home / "xdg"),
                "PATH": os.defpath,
            }
        )
        self._git_hooks_directory = hooks_directory
        try:
            relative_cwd = self.source_cwd.relative_to(self.source_root)
        except ValueError as exc:
            self.close()
            raise WorkflowIsolationError(
                "workflow cwd escaped its source root"
            ) from exc
        self.relative_cwd = relative_cwd
        if not isinstance(baseline_scope_relative_to_cwd, bool):
            self.close()
            raise TypeError("workflow baseline scope flag must be boolean")
        if baseline_scope_paths is not None and (
            not isinstance(baseline_scope_paths, tuple) or not baseline_scope_paths
        ):
            self.close()
            raise WorkflowIsolationError(
                "workflow baseline scope must be a non-empty tuple or null"
            )
        try:
            self._baseline_scope_roots = (
                self._root_relative_scope(
                    baseline_scope_paths,
                    paths_relative_to_cwd=baseline_scope_relative_to_cwd,
                )
                if baseline_scope_paths is not None
                else None
            )
        except BaseException:
            self.close()
            raise
        self.workspace_root = self._temp_root / "workspace"
        try:
            self._snapshot_directory()
            self._validate_snapshot_symlinks()
            source_state_digest = self._filesystem_state_digest(
                self.workspace_root, use_digest_cache=False
            )
            self.cwd = self.workspace_root / relative_cwd
            if not self.cwd.is_dir():
                raise WorkflowIsolationError(
                    "isolated workflow cwd was not materialized"
                )
            self._initialize_isolated_repository()
            self._snapshot_trusted_git_metadata()
            self._source_state_digest = source_state_digest
            # Private diagnostic images are read before workers can change
            # the candidate. They confer no freeze/promotion authority.
            from minicode.failure_evidence import capture_scoped_files

            try:
                self._diagnostic_baseline = (
                    capture_scoped_files(self.workspace_root, self._baseline_scope_roots)
                    if self._baseline_scope_roots is not None else None
                )
            except Exception:  # Diagnostics cannot alter transaction admission.
                self._diagnostic_baseline = None
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "WorkflowWorkspaceTransaction":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            self._temporary = None
            temporary.cleanup()

    def capture_failure_candidate(
        self,
        allowed_paths: tuple[str, ...],
        *,
        paths_relative_to_cwd: bool = True,
    ) -> dict[str, object]:
        """Return bounded diagnostic images, never a frozen change capability.

        The host calls this before close, including after budget/cancellation.
        It cannot execute a command, mutate the candidate, or promote files.
        """
        from minicode.failure_evidence import capture_scoped_files

        normalized = self._root_relative_scope(
            allowed_paths, paths_relative_to_cwd=paths_relative_to_cwd,
        )
        if self._baseline_scope_roots is not None and any(
            not self._path_is_allowed(path, self._baseline_scope_roots)
            for path in normalized
        ):
            raise WorkflowIsolationError("diagnostic capture exceeds original write scope")
        current = capture_scoped_files(self.workspace_root, normalized)
        baseline = getattr(self, "_diagnostic_baseline", None)
        original = baseline["files"] if isinstance(baseline, dict) else {}
        files = []
        captured = current["files"]
        for relative in sorted(set(captured) | set(original)):
            if not self._path_is_allowed(relative, normalized):
                continue
            before = original.get(relative)
            after = captured.get(relative)
            if before is not None and after is not None and before.get("sha256") and before.get("sha256") == after.get("sha256"):
                continue
            if after is None and not current["complete"]:
                continue  # An incomplete scan is not evidence of deletion.
            record = dict(after or {"path": relative, "action": "deleted"})
            if before is not None and before.get("sha256"):
                record["baselineSha256"] = before["sha256"]
            if after is not None:
                record["action"] = "modified" if before else "created" if baseline and baseline["complete"] else "captured"
            if before and "content" in before and (after is None or "content" in after) and len(before["content"]) < 64000 and (after is None or len(after["content"]) < 64000):
                diff = "".join(difflib.unified_diff(
                    before["content"].splitlines(keepends=True),
                    (after["content"] if after else "").splitlines(keepends=True),
                    fromfile="a/" + relative, tofile="b/" + relative,
                ))
                record["diff"] = diff[:24000]
                record["diffTruncated"] = len(diff) > 24000
            files.append(record)
        return {
            "failureCandidateVersion": 1, "promotable": False,
            "baseDigest": self.base_digest, "files": files,
            "complete": current["complete"] and not any("omitted" in item for item in files),
            "baselineComplete": bool(baseline and baseline["complete"]),
        }

    @property
    def parent_promotion_completed(self) -> bool:
        """Whether the approved patch has finished applying to the parent."""

        return self._parent_promotion_completed

    @property
    def parent_promotion_unknown(self) -> bool:
        """Whether an interrupted apply left a non-base, non-target parent."""
        return self._parent_promotion_unknown

    def set_operation_guard(
        self,
        *,
        deadline_monotonic: float | None,
        timeout_seconds: float | None,
        operation_checkpoint: object | None,
    ) -> None:
        if deadline_monotonic is not None and (
            isinstance(deadline_monotonic, bool)
            or not isinstance(deadline_monotonic, (int, float))
            or not math.isfinite(deadline_monotonic)
        ):
            raise TypeError("workflow deadline must be numeric or null")
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not float(timeout_seconds) > 0
        ):
            raise TypeError("workflow timeout must be finite positive or null")
        if operation_checkpoint is not None and not callable(
            operation_checkpoint
        ):
            raise TypeError("workflow checkpoint must be callable or null")
        self._deadline_monotonic = deadline_monotonic
        self._timeout_seconds = float(timeout_seconds) if timeout_seconds is not None else None
        self._operation_checkpoint = operation_checkpoint

    def _commit_checkpoint(self) -> float | None:
        checkpoint = self._operation_checkpoint
        if callable(checkpoint):
            checkpoint()
        if self._deadline_monotonic is None:
            return None
        remaining = float(self._deadline_monotonic) - time.monotonic()
        if remaining <= 0:
            raise TaskDeadlineExceeded(self._timeout_seconds)
        return remaining

    def _acquire_parent_commit_lock(self) -> None:
        """Acquire the promotion lock without hiding cancellation/deadlines."""
        while True:
            remaining = self._commit_checkpoint()
            wait_seconds = 0.1
            if remaining is not None:
                wait_seconds = max(0.001, min(wait_seconds, remaining))
            if _WORKFLOW_PARENT_COMMIT_LOCK.acquire(timeout=wait_seconds):
                return

    @contextmanager
    def _cross_process_commit_lock(self) -> Iterator[None]:
        """Serialize promotions for one source root across local processes."""
        lock_root = _workflow_parent_lock_namespace()
        _prepare_workflow_parent_lock_namespace(lock_root)
        handle = None
        try:
            lock_name = _workflow_parent_lock_key(self.source_root)
            lock_path = lock_root / f"{lock_name}.lock"
            handle = lock_path.open("a+b")
            os.chmod(lock_path, 0o600)
            advisory_lock.prepare_advisory_lock_file(
                handle.fileno(),
                mode=0o600,
            )
        except BaseException as exc:
            if handle is not None:
                try:
                    handle.close()
                except BaseException:
                    # Preserve the setup failure; the descriptor close was
                    # still attempted and must not replace cancellation.
                    pass
            if isinstance(exc, OSError):
                raise WorkflowIsolationError(
                    f"workflow commit lock is unavailable: {exc}"
                ) from exc
            raise

        assert handle is not None
        locked = False
        primary_failure = False
        try:
            while not locked:
                remaining = self._commit_checkpoint()
                try:
                    locked = advisory_lock.acquire_advisory_lock(
                        handle.fileno()
                    )
                except InterruptedError:
                    continue
                except OSError as exc:
                    raise WorkflowIsolationError(
                        f"workflow commit lock is unavailable: {exc}"
                    ) from exc
                if not locked:
                    time.sleep(
                        0.05
                        if remaining is None
                        else max(0.001, min(0.05, remaining))
                    )
            yield
        except BaseException:
            # Acquisition/body failures describe the authoritative promotion
            # state.  Cleanup is still attempted exactly once, but must not
            # replace that primary fact (especially CommitStateUnknown).
            primary_failure = True
            raise
        finally:
            release_failure: BaseException | None = None
            release_traceback = None
            close_failure: BaseException | None = None
            close_traceback = None
            try:
                if locked:
                    advisory_lock.release_advisory_lock(handle.fileno())
            except BaseException as exc:
                release_failure = exc
                release_traceback = exc.__traceback__
            finally:
                try:
                    handle.close()
                except BaseException as exc:
                    close_failure = exc
                    close_traceback = exc.__traceback__
            if not primary_failure:
                if release_failure is not None:
                    raise release_failure.with_traceback(release_traceback)
                if close_failure is not None:
                    raise close_failure.with_traceback(close_traceback)

    def _discover_git_root(self) -> Path | None:
        """Find the nearest worktree root without invoking repository Git."""
        candidate = self.source_cwd
        while True:
            marker = candidate / ".git"
            if marker.is_dir() or marker.is_file():
                return candidate
            parent = candidate.parent
            if parent == candidate:
                return None
            candidate = parent

    def _resolve_host_git_executable(self) -> str:
        """Freeze a host-provided Git binary outside the untrusted workspace."""
        configured = os.environ.get("MINICODE_TRUSTED_GIT_EXECUTABLE")
        if configured:
            configured_path = Path(configured)
            if not configured_path.is_absolute():
                raise WorkflowIsolationError(
                    "MINICODE_TRUSTED_GIT_EXECUTABLE must be absolute"
                )
            candidate = str(configured_path)
        else:
            trusted_path = os.pathsep.join(
                trusted_git_search_directories(source_root=self.source_root)
            )
            candidate = shutil.which("git", path=trusted_path)
        if not candidate:
            raise WorkflowIsolationError("host Git executable is unavailable")
        try:
            resolved = Path(candidate).resolve(strict=True)
        except OSError as exc:
            raise WorkflowIsolationError(
                "host Git executable is unavailable"
            ) from exc
        if (
            resolved.is_relative_to(self.source_root)
            or not resolved.is_file()
            or not os.access(resolved, os.X_OK)
        ):
            raise WorkflowIsolationError(
                "host Git executable resolved inside the workflow workspace"
            )
        return str(resolved)

    def _git_args(self, *args: str) -> list[str]:
        return [
            self._git_executable,
            "-c",
            f"core.hooksPath={self._git_hooks_directory}",
            *args,
        ]

    @staticmethod
    def _command_error(args: list[str], result: subprocess.CompletedProcess) -> str:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = stderr[-600:] if stderr else f"exit {result.returncode}"
        return f"{args[0]} {args[1] if len(args) > 1 else ''}: {detail}"

    def _run_checked(
        self,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        input_bytes: bytes | None = None,
        timeout_seconds: float | None = None,
        use_operation_guard: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        effective_timeout = timeout_seconds
        if use_operation_guard:
            remaining = self._commit_checkpoint()
            effective_timeout = (
                remaining
                if effective_timeout is None
                else (
                    effective_timeout
                    if remaining is None
                    else min(effective_timeout, remaining)
                )
            )
        try:
            result = subprocess.run(
                args,
                cwd=str(cwd),
                env=env,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            if (
                use_operation_guard
                and self._deadline_monotonic is not None
                and time.monotonic() >= self._deadline_monotonic
            ):
                raise TaskDeadlineExceeded(self._timeout_seconds) from exc
            raise WorkflowIsolationError(
                f"{args[0]} could not execute: {exc}"
            ) from exc
        except OSError as exc:
            raise WorkflowIsolationError(
                f"{args[0]} could not execute: {exc}"
            ) from exc
        if use_operation_guard:
            self._commit_checkpoint()
        if check and result.returncode != 0:
            raise WorkflowIsolationError(self._command_error(args, result))
        return result

    def _snapshot_directory(self) -> None:
        self._copytree_with_checkpoints(
            self.source_root,
            self.workspace_root,
            ignore=shutil.ignore_patterns(".git"),
        )

    def _copy_file_with_checkpoints(
        self,
        source: str,
        destination: str,
    ) -> str:
        """Copy one regular file while keeping the global deadline observable."""
        source_path = Path(source)
        destination_path = Path(destination)
        source_stat = source_path.stat(follow_symlinks=False)
        if not stat.S_ISREG(source_stat.st_mode):
            raise WorkflowIsolationError(
                f"workflow snapshot contains an unsupported file: {source_path}"
            )
        self._commit_checkpoint()
        with source_path.open("rb") as reader, destination_path.open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                self._commit_checkpoint()
                writer.write(chunk)
        shutil.copystat(
            source_path,
            destination_path,
            follow_symlinks=False,
        )
        self._commit_checkpoint()
        return str(destination_path)

    def _copytree_with_checkpoints(
        self,
        source: Path,
        destination: Path,
        *,
        ignore: object | None = None,
    ) -> None:
        def checked_ignore(directory: str, names: list[str]) -> set[str]:
            self._commit_checkpoint()
            if callable(ignore):
                return set(ignore(directory, names))
            return set()

        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=checked_ignore,
            copy_function=self._copy_file_with_checkpoints,
        )
        self._commit_checkpoint()

    def _filesystem_state_digest(
        self,
        root: Path,
        *,
        use_operation_guard: bool = True,
        ignored_names: frozenset[str] = _WORKSPACE_IGNORED_ENTRY_NAMES,
        ignored_root_entries: frozenset[str] = frozenset(),
        use_digest_cache: bool = True,
    ) -> str:
        """Hash workspace bytes and entry types without following symlinks."""
        return self._workspace_state_digest(
            root,
            include_directories=True,
            use_operation_guard=use_operation_guard,
            ignored_names=ignored_names,
            ignored_root_entries=ignored_root_entries,
            use_digest_cache=use_digest_cache,
        )

    def _promotion_state_digest(
        self,
        root: Path,
        *,
        use_operation_guard: bool = True,
    ) -> str:
        """Hash only state that the frozen Git patch can promote.

        Git patches carry regular files, symlinks, and executable bits, but not
        standalone directory entries.  Keeping this projection separate from
        the full-tree concurrency baseline makes interrupted-apply recovery
        truthful when the isolated worker happened to create an empty folder.
        """
        return self._workspace_state_digest(
            root,
            include_directories=False,
            use_operation_guard=use_operation_guard,
            ignored_names=_WORKSPACE_IGNORED_ENTRY_NAMES,
            ignored_root_entries=frozenset(),
            use_digest_cache=False,
        )

    def _scoped_filesystem_state_digest(
        self,
        root: Path,
        guard_roots: tuple[str, ...],
        *,
        include_directories: bool = True,
        use_operation_guard: bool = True,
    ) -> str:
        """Hash only the subtrees this workflow may promote into.

        Roots are workspace-root-relative and matched case-sensitively like
        ``_path_is_allowed``.  A scope root's ancestor chain is hashed as
        directory entries so an ancestor swapped for a symlink or file still
        flips the digest; unreadable in-scope entries keep raising, so an
        undecidable scope stays fail-closed.  A "." root degenerates to the
        full-tree digest.  ``include_directories=False`` selects the same
        promotable-state projection as ``_promotion_state_digest``.
        """
        return self._workspace_state_digest(
            root,
            include_directories=include_directories,
            use_operation_guard=use_operation_guard,
            ignored_names=_WORKSPACE_IGNORED_ENTRY_NAMES,
            ignored_root_entries=frozenset(),
            guard_roots=guard_roots,
            use_digest_cache=False,
        )

    def _git_control_state_digest(self, root: Path) -> str:
        """Hash repo-local oracle semantics, excluding staged data/objects."""
        return self._filesystem_state_digest(
            root,
            ignored_names=frozenset(),
            ignored_root_entries=_MUTABLE_GIT_ROOT_ENTRIES,
            use_digest_cache=False,
        )

    def _workspace_state_digest(
        self,
        root: Path,
        *,
        include_directories: bool,
        use_operation_guard: bool,
        ignored_names: frozenset[str],
        ignored_root_entries: frozenset[str],
        guard_roots: tuple[str, ...] | None = None,
        use_digest_cache: bool = True,
    ) -> str:
        digest = hashlib.sha256()
        digest_cache = shared_workspace_digest_cache()

        def checkpoint() -> None:
            if use_operation_guard:
                self._commit_checkpoint()

        def guarded(relative: str) -> bool:
            if guard_roots is None:
                return True
            return any(
                guard_root == "."
                or relative == guard_root
                or relative.startswith(guard_root.rstrip("/") + "/")
                or guard_root.startswith(relative + "/")
                for guard_root in guard_roots
            )

        def visit(directory: Path, relative_parent: Path) -> None:
            checkpoint()
            try:
                entries = sorted(
                    os.scandir(directory),
                    key=lambda entry: os.fsencode(entry.name),
                )
            except OSError as exc:
                raise WorkflowIsolationError(
                    f"workflow state could not be read: {directory}"
                ) from exc
            for entry in entries:
                checkpoint()
                if entry.name in ignored_names or (
                    relative_parent == Path()
                    and entry.name in ignored_root_entries
                ):
                    continue
                path = Path(entry.path)
                relative_path = relative_parent / entry.name
                if not guarded(relative_path.as_posix()):
                    # Outside the declared scope nothing below can be guarded:
                    # an unguarded directory contains no scope root, and no
                    # scope root contains it.
                    continue
                relative = os.fsencode(relative_path.as_posix())
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise WorkflowIsolationError(
                        f"workflow state could not be read: {path}"
                    ) from exc
                def hash_path() -> None:
                    digest.update(len(relative).to_bytes(8, "big"))
                    digest.update(relative)

                if stat.S_ISLNK(metadata.st_mode):
                    hash_path()
                    target = os.fsencode(os.readlink(path))
                    digest.update(b"L")
                    digest.update(len(target).to_bytes(8, "big"))
                    digest.update(target)
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if include_directories:
                        hash_path()
                        digest.update(b"D")
                    visit(path, relative_path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise WorkflowIsolationError(
                        f"workflow state contains an unsupported file: {path}"
                    )
                hash_path()
                digest.update(b"F")
                executable = metadata.st_mode & (
                    stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
                digest.update(b"X" if executable else b"-")
                # Cache policy cannot alter byte identity or tree encoding.
                # Authority gates always read each file afresh; diagnostic
                # walks may reuse the same unchanged filesystem object.
                digest.update(b"H")
                try:
                    content_digest = (
                        digest_cache.content_digest(
                            path,
                            relative_posix=relative_path.as_posix(),
                            size=metadata.st_size,
                            mtime_ns=metadata.st_mtime_ns,
                            checkpoint=checkpoint,
                            use_cache=use_digest_cache,
                        )
                    )
                except OSError as exc:
                    raise WorkflowIsolationError(
                        f"workflow state could not be read consistently: {path}"
                    ) from exc
                digest.update(content_digest)

        visit(root, Path())
        return "sha256:" + digest.hexdigest()

    def _validate_parent_baseline(self) -> None:
        """Re-check that the parent still matches the snapshot baseline.

        Workers and verification may depend on any snapshotted file, including
        paths outside the declared write scope. Disjoint writes therefore do
        not establish independent changes. Until read dependencies are proven,
        any parent drift invalidates the frozen verification result.
        """
        current = self._filesystem_state_digest(
            self.source_root, use_digest_cache=False
        )
        if current != self._source_state_digest:
            raise WorkflowIsolationError(
                "parent workspace changed after workflow snapshot; "
                "rerun required"
            )

    def _validate_snapshot_symlinks(self, root: Path | None = None) -> None:
        """Reject links that could route isolated writes into the parent."""
        snapshot_root = self.workspace_root if root is None else root
        canonical_root = snapshot_root.resolve(strict=True)
        for directory, dirnames, filenames in os.walk(
            snapshot_root,
            followlinks=False,
        ):
            self._commit_checkpoint()
            base = Path(directory)
            for name in [*dirnames, *filenames]:
                self._commit_checkpoint()
                candidate = base / name
                if not candidate.is_symlink():
                    continue
                try:
                    resolved = candidate.resolve(strict=False)
                    contained = resolved.is_relative_to(canonical_root)
                except (OSError, RuntimeError, ValueError):
                    contained = False
                if not contained:
                    raise WorkflowIsolationError(
                        f"snapshot contains an escaping symlink: "
                        f"{candidate.relative_to(snapshot_root)}"
                    )

    def _initialize_isolated_repository(self) -> None:
        self._run_checked(
            self._git_args("init", "-q"),
            cwd=self.workspace_root,
            env=self._git_environment,
        )
        self._run_checked(
            self._git_args("add", "-A", "-f", "--", "."),
            cwd=self.workspace_root,
            env=self._git_environment,
        )
        self._run_checked(
            self._git_args(
                "-c",
                "user.name=MiniCode Workflow",
                "-c",
                "user.email=workflow@minicode.invalid",
                "commit",
                "-q",
                "--no-verify",
                "--allow-empty",
                "-m",
                "workflow baseline",
            ),
            cwd=self.workspace_root,
            env=self._git_environment,
        )
        self._baseline = self._run_checked(
            self._git_args("rev-parse", "HEAD"),
            cwd=self.workspace_root,
            env=self._git_environment,
        ).stdout.decode("ascii").strip()
        baseline_tree = self._run_checked(
            self._git_args(
                "ls-tree",
                "-r",
                "--full-tree",
                "-z",
                self._baseline,
            ),
            cwd=self.workspace_root,
            env=self._git_environment,
        ).stdout
        self.base_digest = "sha256:" + hashlib.sha256(baseline_tree).hexdigest()

    def _snapshot_trusted_git_metadata(self) -> None:
        """Preserve scheduler-created baseline metadata before worker access."""
        self._trusted_git_root = self._temp_root / "trusted-baseline-git"
        self._copytree_with_checkpoints(
            self.workspace_root / ".git",
            self._trusted_git_root,
        )
        self._trusted_git_state_digest = self._filesystem_state_digest(
            self._trusted_git_root,
            ignored_names=frozenset(),
            use_digest_cache=False,
        )
        self._trusted_git_control_digest = self._git_control_state_digest(
            self._trusted_git_root
        )

    def _materialize_trusted_git_metadata(
        self,
        destination_git: Path,
    ) -> None:
        """Clone scheduler-owned baseline metadata and verify it exactly."""
        try:
            if (
                self._filesystem_state_digest(
                    self._trusted_git_root,
                    ignored_names=frozenset(),
                    use_digest_cache=False,
                )
                != self._trusted_git_state_digest
            ):
                raise WorkflowIsolationError(
                    "workflow trusted Git metadata changed after initialization"
                )
            self._copytree_with_checkpoints(
                self._trusted_git_root,
                destination_git,
            )
            if (
                self._filesystem_state_digest(
                    self._trusted_git_root,
                    ignored_names=frozenset(),
                    use_digest_cache=False,
                )
                != self._trusted_git_state_digest
                or self._filesystem_state_digest(
                    destination_git,
                    ignored_names=frozenset(),
                    use_digest_cache=False,
                )
                != self._trusted_git_state_digest
            ):
                raise WorkflowIsolationError(
                    "workflow trusted Git metadata changed while cloning"
                )
        except WorkflowIsolationError:
            raise
        except OSError as exc:
            raise WorkflowIsolationError(
                "workflow trusted Git metadata could not be cloned"
            ) from exc

    def _materialize_trusted_repository(
        self,
        source_root: Path,
        destination_root: Path,
    ) -> None:
        """Copy worktree data and attach only clean scheduler Git metadata."""
        self._validate_snapshot_symlinks(source_root)
        self._copytree_with_checkpoints(
            source_root,
            destination_root,
            ignore=shutil.ignore_patterns(".git"),
        )
        self._materialize_trusted_git_metadata(destination_root / ".git")

    def remap_text(self, text: str) -> str:
        """Replace parent absolute paths before giving the task to agents."""
        mapped = str(text)
        replacements = sorted(
            {
                (str(self.source_root), str(self.workspace_root)),
                (str(self.source_cwd), str(self.cwd)),
            },
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        for source, target in replacements:
            mapped = mapped.replace(source, target)
        return mapped

    @property
    def trusted_git_executable(self) -> str:
        """Host-frozen Git capability for the internal verification oracle."""
        return self._git_executable

    def _sanitize_verification_workspace(
        self,
        root: Path,
        *,
        read_roots: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
        paths_relative_to_cwd: bool,
        retain_git_metadata: bool,
    ) -> None:
        """Physically remove data outside the Verifier's read authority."""
        from minicode.scheduler_permissions import (
            is_mandatory_scheduler_forbidden_path,
        )

        if not isinstance(read_roots, tuple) or not read_roots:
            raise WorkflowIsolationError(
                "verification read roots must be a non-empty tuple"
            )
        if not isinstance(forbidden_paths, tuple):
            raise WorkflowIsolationError(
                "verification forbidden paths must be a tuple"
            )
        normalized_read_roots = self._root_relative_scope(
            read_roots,
            paths_relative_to_cwd=paths_relative_to_cwd,
        )
        normalized_forbidden = self._root_relative_scope(
            forbidden_paths,
            paths_relative_to_cwd=paths_relative_to_cwd,
        )
        for normalized in normalized_forbidden:
            if normalized == ".":
                raise WorkflowIsolationError(
                    "verification scope forbids the entire workspace"
                )
        folded_read_roots = tuple(path.casefold() for path in normalized_read_roots)
        folded_forbidden = tuple(path.casefold() for path in normalized_forbidden)

        def within_read_scope(relative: str, *, directory: bool) -> bool:
            folded = relative.casefold()
            if retain_git_metadata and (
                folded == ".git" or folded.startswith(".git/")
            ):
                return True
            return any(
                allowed == "."
                or folded == allowed
                or folded.startswith(allowed.rstrip("/") + "/")
                or (
                    directory
                    and allowed.startswith(folded.rstrip("/") + "/")
                )
                for allowed in folded_read_roots
            )

        def forbidden(relative: str) -> bool:
            folded = relative.casefold()
            if retain_git_metadata and (
                folded == ".git" or folded.startswith(".git/")
            ):
                return False
            return is_mandatory_scheduler_forbidden_path(relative) or any(
                folded == item or folded.startswith(item.rstrip("/") + "/")
                for item in folded_forbidden
            )

        for directory, dirnames, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            self._commit_checkpoint()
            base = Path(directory)
            for name in tuple(dirnames):
                self._commit_checkpoint()
                candidate = base / name
                relative = candidate.relative_to(root).as_posix()
                if not forbidden(relative) and within_read_scope(
                    relative,
                    directory=True,
                ):
                    continue
                dirnames.remove(name)
                if candidate.is_symlink():
                    _delete_workspace_entry(candidate)
                else:
                    self._remove_tree_with_checkpoints(candidate)
            for name in filenames:
                self._commit_checkpoint()
                candidate = base / name
                relative = candidate.relative_to(root).as_posix()
                if forbidden(relative) or not within_read_scope(
                    relative,
                    directory=False,
                ):
                    _delete_workspace_entry(candidate)

    def _remove_tree_with_checkpoints(self, root: Path) -> None:
        if root.is_symlink():
            _delete_workspace_entry(root)
            return
        for directory, dirnames, filenames in os.walk(
            root,
            topdown=False,
            followlinks=False,
        ):
            self._commit_checkpoint()
            base = Path(directory)
            for name in filenames:
                self._commit_checkpoint()
                _delete_workspace_entry(base / name)
            for name in dirnames:
                self._commit_checkpoint()
                child = base / name
                if child.is_symlink():
                    _delete_workspace_entry(child)
                else:
                    _delete_workspace_entry(child, directory=True)
        _delete_workspace_entry(root, directory=True)
        self._commit_checkpoint()

    @staticmethod
    def _normalize_allowed_path(path: object) -> str:
        if not isinstance(path, str):
            raise WorkflowIsolationError("workflow allowed path is invalid")
        value = path.strip().replace("\\", "/")
        if not value or value.startswith("/"):
            raise WorkflowIsolationError("workflow allowed path is invalid")
        parts = [part for part in value.split("/") if part not in {"", "."}]
        if ".." in parts:
            raise WorkflowIsolationError("workflow allowed path is invalid")
        return "/".join(parts) or "."

    @staticmethod
    def _path_is_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
        return any(
            allowed == "."
            or path == allowed
            or path.startswith(allowed.rstrip("/") + "/")
            for allowed in allowed_paths
        )

    def _root_relative_scope(
        self,
        allowed_paths: tuple[str, ...],
        *,
        paths_relative_to_cwd: bool,
    ) -> tuple[str, ...]:
        normalized = tuple(
            self._normalize_allowed_path(path) for path in allowed_paths
        )
        if not paths_relative_to_cwd or self.relative_cwd == Path("."):
            return normalized
        return tuple(
            self.relative_cwd.as_posix()
            if path == "."
            else (self.relative_cwd / path).as_posix()
            for path in normalized
        )

    def _scope_relative_files(
        self,
        changed: list[dict[str, str]],
        *,
        paths_relative_to_cwd: bool,
    ) -> list[dict[str, str]]:
        if not paths_relative_to_cwd or self.relative_cwd == Path("."):
            return changed
        projected: list[dict[str, str]] = []
        for item in changed:
            try:
                path = Path(item["path"]).relative_to(self.relative_cwd)
            except ValueError as exc:
                raise WorkflowIsolationError(
                    "workflow changed path is outside invocation cwd"
                ) from exc
            projected.append(
                {"path": path.as_posix(), "action": item["action"]}
            )
        return projected

    def _staged_patch(self, workspace_root: Path | None = None) -> bytes:
        root = self.workspace_root if workspace_root is None else workspace_root
        self._validate_snapshot_symlinks(root)
        self._run_checked(
            self._git_args("add", "-A", "-f", "--", "."),
            cwd=root,
            env=self._git_environment,
        )
        return self._run_checked(
            self._git_args(
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                self._baseline,
                "--",
            ),
            cwd=root,
            env=self._git_environment,
        ).stdout

    def _changed_files(
        self,
        workspace_root: Path | None = None,
    ) -> list[dict[str, str]]:
        root = self.workspace_root if workspace_root is None else workspace_root
        output = self._run_checked(
            self._git_args(
                "diff",
                "--cached",
                "--name-status",
                "--no-renames",
                "-z",
                self._baseline,
                "--",
            ),
            cwd=root,
            env=self._git_environment,
        ).stdout
        fields = output.decode("utf-8", errors="surrogateescape").split("\0")
        if fields and not fields[-1]:
            fields.pop()
        if len(fields) % 2:
            raise WorkflowIsolationError("workflow changed-file list is malformed")
        action_by_status = {
            "A": "created",
            "D": "deleted",
            "M": "modified",
            "T": "modified",
        }
        changed: list[dict[str, str]] = []
        for index in range(0, len(fields), 2):
            status = fields[index][:1]
            path = fields[index + 1]
            action = action_by_status.get(status)
            if action is None:
                raise WorkflowIsolationError(
                    f"workflow changed-file status is unsupported: {status}"
                )
            changed.append({"path": path, "action": action})
        return changed

    def resume_for_repair(self, frozen: FrozenWorkflowChange) -> None:
        """Revoke one unpromoted frozen capability before a bounded host repair.

        The original snapshot baseline and write bounds remain unchanged.
        Previously published evidence stays immutable; this only reopens the
        isolated working copy, and the old capability can never promote again.
        """
        with self._transaction_lock:
            if (
                self._temporary is None
                or self._frozen_change is not frozen
                or self._parent_promotion_completed
                or self._parent_promotion_unknown
            ):
                raise WorkflowIsolationError("workflow change cannot be reopened for repair")
            if self._filesystem_state_digest(
                self.workspace_root, use_digest_cache=False
            ) != frozen._frozen_state_digest:
                raise WorkflowIsolationError("workflow frozen workspace changed before repair")
            self._commit_checkpoint()
            self._frozen_change = None

    def freeze(
        self,
        *,
        allowed_paths: tuple[str, ...],
        paths_relative_to_cwd: bool = False,
    ) -> FrozenWorkflowChange:
        """End mutation and return the exact scoped change capability."""
        with self._transaction_lock:
            if self._temporary is None:
                raise WorkflowIsolationError("workflow transaction is closed")
            if self._frozen_change is not None:
                raise WorkflowIsolationError(
                    "workflow change set was already frozen"
                )
            normalized_allowed = self._root_relative_scope(
                allowed_paths,
                paths_relative_to_cwd=paths_relative_to_cwd,
            )
            source_state_digest = self._filesystem_state_digest(
                self.workspace_root, use_digest_cache=False
            )
            source_target_digest = self._promotion_state_digest(
                self.workspace_root
            )
            frozen_container: Path | None = None
            try:
                frozen_container = Path(
                    tempfile.mkdtemp(
                        prefix="frozen-workspace-",
                        dir=self._temp_root,
                    )
                )
                frozen_workspace_root = frozen_container / "workspace"
                self._materialize_trusted_repository(
                    self.workspace_root,
                    frozen_workspace_root,
                )
                frozen_state_digest = self._filesystem_state_digest(
                    frozen_workspace_root, use_digest_cache=False
                )
                target_state_digest = self._promotion_state_digest(
                    frozen_workspace_root
                )
                if (
                    frozen_state_digest != source_state_digest
                    or target_state_digest != source_target_digest
                ):
                    raise WorkflowIsolationError(
                        "workflow workspace changed while freezing"
                    )
                scoped_target_state_digest = (
                    self._scoped_filesystem_state_digest(
                        frozen_workspace_root,
                        self._baseline_scope_roots,
                        include_directories=False,
                    )
                    if self._baseline_scope_roots is not None
                    else None
                )
                patch = self._staged_patch(frozen_workspace_root)
                changed = self._changed_files(frozen_workspace_root)
                if (
                    self._filesystem_state_digest(
                        self.workspace_root, use_digest_cache=False
                    )
                    != source_state_digest
                    or self._git_control_state_digest(
                        frozen_workspace_root / ".git"
                    )
                    != self._trusted_git_control_digest
                    or self._filesystem_state_digest(
                        frozen_workspace_root, use_digest_cache=False
                    )
                    != frozen_state_digest
                ):
                    raise WorkflowIsolationError(
                        "workflow workspace changed while freezing"
                    )
                outside = [
                    item["path"]
                    for item in changed
                    if not self._path_is_allowed(
                        item["path"],
                        normalized_allowed,
                    )
                ]
                if outside:
                    raise WorkflowIsolationError(
                        "workflow changed paths are outside allowed paths: "
                        + ", ".join(outside[:10])
                    )
                if self._baseline_scope_roots is not None:
                    unguarded = [
                        item["path"]
                        for item in changed
                        if not self._path_is_allowed(
                            item["path"],
                            self._baseline_scope_roots,
                        )
                    ]
                    if unguarded:
                        raise WorkflowIsolationError(
                            "workflow changed paths exceed the promotion "
                            "baseline scope: " + ", ".join(unguarded[:10])
                        )
                scoped_files = self._scope_relative_files(
                    changed,
                    paths_relative_to_cwd=paths_relative_to_cwd,
                )
            except BaseException as exc:
                if frozen_container is not None:
                    shutil.rmtree(frozen_container, ignore_errors=True)
                if isinstance(exc, OSError):
                    raise WorkflowIsolationError(
                        "workflow frozen snapshot could not be created"
                    ) from exc
                raise

            patch_digest = "sha256:" + hashlib.sha256(patch).hexdigest()
            frozen = FrozenWorkflowChange._create(
                owner=self,
                patch=patch,
                patch_digest=patch_digest,
                base_digest=self.base_digest,
                target_state_digest=target_state_digest,
                scoped_target_state_digest=scoped_target_state_digest,
                paths_relative_to_cwd=paths_relative_to_cwd,
                files=tuple(
                    (item["path"], item["action"])
                    for item in scoped_files
                ),
                frozen_state_digest=frozen_state_digest,
                frozen_workspace_root=frozen_workspace_root,
                relative_cwd=self.relative_cwd,
            )
            self._frozen_change = frozen
            return frozen


def _frozen_diff_document(
    *,
    patch: bytes,
    base_digest: str,
    patch_digest: str,
) -> dict[str, object]:
    """Keep complete patch data within the workflow's storage allowance."""
    if len(patch) <= MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES:
        try:
            full_diff = patch.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            document = {
                "diffVersion": 1,
                "baseDigest": base_digest,
                "patchDigest": patch_digest,
                "byteCount": len(patch),
                "complete": True,
                "encoding": "utf-8",
                "diff": full_diff,
            }
            try:
                # Use the host data path, not the model control-contract cap.
                # JSON escaping and metadata still count toward total storage.
                _canonical_json(document, allow_frozen_diff=True)
            except SchedulerArtifactError:
                pass
            else:
                return document
    limit = 12_000
    complete = len(patch) <= limit
    if complete:
        preview = patch
        try:
            diff = patch.decode("utf-8")
        except UnicodeDecodeError:
            complete = False
            diff = base64.b64encode(preview).decode("ascii")
            encoding = "base64"
        else:
            encoding = "utf-8"
    else:
        half = limit // 2
        preview = patch[:half] + b"\n...<truncated>...\n" + patch[-half:]
        try:
            diff = preview.decode("utf-8")
        except UnicodeDecodeError:
            diff = base64.b64encode(preview).decode("ascii")
            encoding = "base64"
        else:
            encoding = "utf-8"
    if (
        encoding == "utf-8"
        and len(json.dumps(diff, ensure_ascii=False).encode("utf-8")) > 17_000
    ):
        # JSON escaping can make an otherwise bounded text preview exceed the
        # artifact channel's byte cap. Base64 stays exact and bounded; marking
        # it incomplete prevents approval of an unreadable representation.
        diff = base64.b64encode(preview).decode("ascii")
        encoding = "base64"
        complete = False
    return {
        "diffVersion": 1,
        "baseDigest": base_digest,
        "patchDigest": patch_digest,
        "byteCount": len(patch),
        "complete": complete,
        "encoding": encoding,
        "diff": diff,
    }


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class FrozenWorkflowChange:
    """Digest-bound capability for verification evidence and promotion."""

    _owner: WorkflowWorkspaceTransaction
    _patch: bytes
    _patch_digest: str
    _base_digest: str
    _target_state_digest: str
    _scoped_target_state_digest: str | None
    _paths_relative_to_cwd: bool
    _files: tuple[tuple[str, str], ...]
    _frozen_state_digest: str
    _frozen_workspace_root: Path
    _relative_cwd: Path

    def __init__(self) -> None:
        raise TypeError(
            "FrozenWorkflowChange instances are created by "
            "WorkflowWorkspaceTransaction.freeze()"
        )

    @classmethod
    def _create(
        cls,
        *,
        owner: WorkflowWorkspaceTransaction,
        patch: bytes,
        patch_digest: str,
        base_digest: str,
        target_state_digest: str,
        scoped_target_state_digest: str | None,
        paths_relative_to_cwd: bool,
        files: tuple[tuple[str, str], ...],
        frozen_state_digest: str,
        frozen_workspace_root: Path,
        relative_cwd: Path,
    ) -> FrozenWorkflowChange:
        capability = object.__new__(cls)
        values = {
            "_owner": owner,
            "_patch": patch,
            "_patch_digest": patch_digest,
            "_base_digest": base_digest,
            "_target_state_digest": target_state_digest,
            "_scoped_target_state_digest": scoped_target_state_digest,
            "_paths_relative_to_cwd": paths_relative_to_cwd,
            "_files": files,
            "_frozen_state_digest": frozen_state_digest,
            "_frozen_workspace_root": frozen_workspace_root,
            "_relative_cwd": relative_cwd,
        }
        for name, value in values.items():
            object.__setattr__(capability, name, value)
        return capability

    @contextmanager
    def _owner_session(self) -> Iterator[WorkflowWorkspaceTransaction]:
        owner = getattr(self, "_owner", None)
        if not isinstance(owner, WorkflowWorkspaceTransaction):
            raise WorkflowIsolationError(
                "workflow frozen change owner is unavailable"
            )
        with owner._transaction_lock:
            if (
                owner._temporary is None
                or owner._frozen_change is not self
            ):
                raise WorkflowIsolationError(
                    "workflow frozen change owner is unavailable"
                )
            yield owner

    @property
    def patch_digest(self) -> str:
        with self._owner_session():
            return self._patch_digest

    @property
    def change_set_document(self) -> dict[str, object]:
        """Return a detached JSON projection of this frozen change."""
        with self._owner_session():
            return {
                "workspaceRef": "isolated://workflow",
                "baseDigest": self._base_digest,
                "patchDigest": self._patch_digest,
                "files": [
                    {"path": path, "action": action}
                    for path, action in self._files
                ],
            }

    @property
    def diff_artifact_document(self) -> dict[str, object]:
        """Return detached review evidence for the exact patch."""
        with self._owner_session():
            patch = self._patch
            base_digest = self._base_digest
            patch_digest = self._patch_digest
        return _frozen_diff_document(
            patch=patch,
            base_digest=base_digest,
            patch_digest=patch_digest,
        )

    @contextmanager
    def verification_workspace(
        self,
        *,
        read_roots: tuple[str, ...] = (".",),
        forbidden_paths: tuple[str, ...] = (),
        retain_git_metadata: bool = False,
    ) -> Iterator[Path]:
        """Clone exact frozen bytes so checks cannot alter promotion bytes."""
        with self._owner_session() as owner:
            owner._validate_snapshot_symlinks(self._frozen_workspace_root)
            if (
                owner._filesystem_state_digest(
                    self._frozen_workspace_root, use_digest_cache=False
                )
                != self._frozen_state_digest
            ):
                raise WorkflowIsolationError(
                    "workflow frozen workspace changed after freeze"
                )
            try:
                temporary = tempfile.TemporaryDirectory(
                    prefix="verification-",
                    dir=owner._temp_root,
                )
            except OSError as exc:
                raise WorkflowIsolationError(
                    "workflow verification workspace could not be created"
                ) from exc
            verification_root = Path(temporary.name) / "workspace"
            try:
                owner._materialize_trusted_repository(
                    self._frozen_workspace_root,
                    verification_root,
                )
                if (
                    owner._filesystem_state_digest(
                        verification_root, use_digest_cache=False
                    )
                    != self._frozen_state_digest
                    or owner._staged_patch(verification_root) != self._patch
                    or owner._git_control_state_digest(
                        verification_root / ".git"
                    )
                    != owner._trusted_git_control_digest
                    or owner._filesystem_state_digest(
                        verification_root, use_digest_cache=False
                    )
                    != self._frozen_state_digest
                ):
                    raise WorkflowIsolationError(
                        "workflow verification workspace differs from frozen change"
                    )
                owner._sanitize_verification_workspace(
                    verification_root,
                    read_roots=read_roots,
                    forbidden_paths=forbidden_paths,
                    paths_relative_to_cwd=self._paths_relative_to_cwd,
                    retain_git_metadata=retain_git_metadata,
                )
            except BaseException:
                temporary.cleanup()
                raise
        try:
            verification_cwd = verification_root / self._relative_cwd
            if not verification_cwd.is_dir():
                raise WorkflowIsolationError(
                    "verification workspace cwd was not materialized"
                )
            yield verification_cwd
        finally:
            temporary.cleanup()

    def promote(self, *, approved_patch_digest: object) -> None:
        """Apply this externally approved frozen delta to the parent."""
        with self._owner_session() as owner:
            if approved_patch_digest != self._patch_digest:
                raise WorkflowIsolationError(
                    "workflow patch digest changed after verification"
                )
            if (
                owner._filesystem_state_digest(
                    owner.workspace_root, use_digest_cache=False
                )
                != self._frozen_state_digest
                or owner._promotion_state_digest(owner.workspace_root)
                != self._target_state_digest
            ):
                raise WorkflowIsolationError(
                    "workflow patch digest changed after verification"
                )
            patch = self._patch
        if not patch:
            # A no-op still carries verification claims about its snapshot.
            # Validate those dependencies under the same locks as a write.
            owner._acquire_parent_commit_lock()
            try:
                with owner._cross_process_commit_lock():
                    owner._validate_parent_baseline()
                    owner._commit_checkpoint()
                    owner._parent_promotion_completed = True
            finally:
                _WORKFLOW_PARENT_COMMIT_LOCK.release()
            return
        apply_args = owner._git_args(
            "apply",
            "--binary",
            "--whitespace=nowarn",
            "-",
        )
        apply_check_args = owner._git_args(
            "apply",
            "--check",
            "--binary",
            "--whitespace=nowarn",
            "-",
        )
        try:
            promotion_metadata = tempfile.TemporaryDirectory(
                prefix="promotion-git-",
                dir=owner._temp_root,
            )
        except OSError as exc:
            raise WorkflowIsolationError(
                "workflow promotion metadata could not be created"
            ) from exc
        try:
            promotion_git = Path(promotion_metadata.name) / ".git"
            owner._materialize_trusted_git_metadata(promotion_git)
            parent_git_environment = dict(owner._git_environment)
            parent_git_environment.update(
                {
                    "GIT_DIR": str(promotion_git),
                    "GIT_WORK_TREE": str(owner.source_root),
                }
            )
            owner._acquire_parent_commit_lock()
            try:

                def parent_matches_target() -> bool:
                    # Reconcile the bytes this promotion actually writes.
                    # This cannot authorize a stale snapshot: the complete
                    # parent baseline is checked below before applying.
                    if self._scoped_target_state_digest is not None:
                        return (
                            owner._scoped_filesystem_state_digest(
                                owner.source_root,
                                owner._baseline_scope_roots,
                                include_directories=False,
                                use_operation_guard=False,
                            )
                            == self._scoped_target_state_digest
                        )
                    return (
                        owner._promotion_state_digest(
                            owner.source_root,
                            use_operation_guard=False,
                        )
                        == self._target_state_digest
                    )

                with owner._cross_process_commit_lock():
                    owner._validate_parent_baseline()
                    remaining = owner._commit_checkpoint()
                    owner._run_checked(
                        apply_check_args,
                        cwd=owner.source_root,
                        env=parent_git_environment,
                        input_bytes=patch,
                        timeout_seconds=remaining,
                    )
                    # This is the final cooperative checkpoint. Once the
                    # actual apply starts, finish the short local promotion and
                    # report its real outcome instead of claiming a timeout
                    # with unknown writes.
                    owner._commit_checkpoint()
                    # The write is unresolved from this point until either the
                    # apply returns or exact reconciliation finishes. This
                    # sentinel survives a second BaseException during recovery.
                    owner._parent_promotion_unknown = True
                    try:
                        owner._run_checked(
                            apply_args,
                            cwd=owner.source_root,
                            env=parent_git_environment,
                            input_bytes=patch,
                            use_operation_guard=False,
                        )
                        if not parent_matches_target():
                            raise WorkflowIsolationError(
                                "workflow parent state differs after promotion"
                            )
                        owner._parent_promotion_completed = True
                        owner._parent_promotion_unknown = False
                    except BaseException:
                        try:
                            target_reached = parent_matches_target()
                        except Exception:  # noqa: BLE001 - conservatively unknown
                            owner._parent_promotion_unknown = True
                        else:
                            if target_reached:
                                owner._parent_promotion_completed = True
                                owner._parent_promotion_unknown = False
                            else:
                                full_parent_state = owner._filesystem_state_digest(
                                    owner.source_root,
                                    use_operation_guard=False,
                                    use_digest_cache=False,
                                )
                                if full_parent_state == owner._source_state_digest:
                                    owner._parent_promotion_unknown = False
                        raise
            finally:
                _WORKFLOW_PARENT_COMMIT_LOCK.release()
        finally:
            promotion_metadata.cleanup()


__all__ = [
    "FrozenWorkflowChange",
    "WorkflowIsolationError",
    "WorkflowWorkspaceTransaction",
]
