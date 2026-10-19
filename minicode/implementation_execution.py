"""Host-bound implementation inputs shared by primary and workflow workers.

This contract supplies evidence and approved checks, never write permission or
promotion authority. Model text cannot construct it through a tool call.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from minicode.deterministic_verifier import VerificationCheck
from minicode.task_deadline import raise_task_deadline


def implementation_candidate_digest(spec, context, *, snapshot_root=None):
    """Bind checks to readable candidate inputs, not just writable outputs.

    Public tests, configuration and imported source can invalidate a pass too.
    Snapshot checks and final admission share this framing. Metadata, credentials
    and inert interpreter/test caches never become a read channel through hashing.
    """
    from minicode.permissions import PermissionDeniedError
    from minicode.scheduler_permissions import is_mandatory_scheduler_forbidden_path

    root = Path(snapshot_root or context.cwd).resolve()
    owner_root = Path(context.cwd).resolve()
    permission = getattr(context.permissions, "ensure_path_access", None)
    digest = hashlib.sha256()
    caches = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

    def permitted(relative):
        if is_mandatory_scheduler_forbidden_path(relative):
            return False
        original = owner_root / relative
        if not original.resolve().is_relative_to(owner_root):
            return False
        if callable(permission):
            try:
                permission(str(original.resolve()), "read")
            except (PermissionError, PermissionDeniedError):
                return False
        return True

    def record(relative, kind, data):
        for value in (relative.encode(), kind, data):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)

    for relative in sorted(spec.write_paths):
        if not permitted(relative):
            raise ValueError("candidate scope includes an unreadable path")
        record(relative, b"scope", b"")
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        raise_task_deadline(context)
        base = Path(directory)
        names = sorted([*subdirs, *filenames])
        # An explicit file reader need not grant independent directory access.
        # Traverse namespace here; authorize each file before reading its bytes.
        # Otherwise allowed nested files disappear from candidate identity.
        subdirs[:] = sorted(name for name in subdirs if name not in caches
                           and not is_mandatory_scheduler_forbidden_path(
                               (base / name).relative_to(root).as_posix()))
        for name in names:
            if name in caches:
                continue
            path = base / name
            relative = path.relative_to(root).as_posix()
            if not permitted(relative):
                continue
            raise_task_deadline(context)
            if path.is_symlink():
                record(relative, b"symlink", os.readlink(path).encode())
            elif path.is_file():
                record(relative, b"file", path.read_bytes())
            elif path.is_dir():
                record(relative, b"directory", b"")
            else:
                raise ValueError("candidate includes a nonregular readable input")
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class ImplementationSpec:
    write_paths: tuple[str, ...]
    checks: tuple[VerificationCheck, ...]
    objective: str

    def __post_init__(self):
        if not self.write_paths or any(not isinstance(p, str) or not p or Path(p).is_absolute()
                                       or ".." in Path(p).parts for p in self.write_paths):
            raise ValueError("implementation paths must be explicit workspace-relative paths")
        if any(not isinstance(c, VerificationCheck) or c.kind == "oracle" for c in self.checks):
            raise ValueError("implementation feedback requires host-selected public checks")

    @property
    def test_paths(self) -> tuple[str, ...]:
        return tuple(path for path in self.write_paths
                     if any(part in {"tests", "test", "__tests__"} for part in Path(path).parts[:-1])
                     or Path(path).name.startswith("test_")
                     or Path(path).stem.endswith(("_test", ".test", ".spec")))


def direct_verification_capability(spec: ImplementationSpec):
    """Snapshot the current primary workspace before running each approved check."""
    from minicode.executor_verification import ExecutorCheckCapability
    from minicode.permissions import PermissionManager, PermissionDeniedError
    from minicode.scheduler_permissions import is_mandatory_scheduler_forbidden_path
    from minicode.workflow_workspace import WorkflowWorkspaceTransaction
    from minicode.verification_output_tool import VerificationOutputAccess, VerificationOutputLedger

    outputs = VerificationOutputLedger("wf_direct_implementation")
    output_reader = VerificationOutputAccess(outputs, checkpoint=raise_task_deadline, owns_ledger=True)

    def run_check(check, context):
        from minicode.workflow_runner import _deterministic_verifier_for_context

        raise_task_deadline(context)
        output_reader.bind_direct_owner(context)
        output_manifests = []

        def capture_output(manifest):
            outputs.record(manifest)
            output_manifests.append(manifest)

        with WorkflowWorkspaceTransaction(
            context.cwd, deadline_monotonic=context._task_deadline_monotonic,
            operation_checkpoint=lambda: raise_task_deadline(context),
        ) as transaction:
            # The primary may have a narrower reader than the whole repository.
            # Project that existing authority onto the verification clone; an
            # approved pytest command must not become an indirect read channel.
            denied_paths = []
            permissions = context.permissions
            check_path = getattr(permissions, "ensure_path_access", None)
            if callable(check_path):
                for directory, subdirs, filenames in os.walk(transaction.cwd, followlinks=False):
                    raise_task_deadline(context)
                    base = Path(directory)
                    subdirs[:] = [name for name in subdirs
                                  if not is_mandatory_scheduler_forbidden_path(
                                      (base / name).relative_to(transaction.cwd).as_posix())]
                    # Directory symlinks are not traversed. Check their resolved
                    # original target so an alias cannot evade the parent scope.
                    entries = [*filenames, *(name for name in subdirs if (base / name).is_symlink())]
                    for name in entries:
                        raise_task_deadline(context)
                        relative = (base / name).relative_to(transaction.cwd)
                        if is_mandatory_scheduler_forbidden_path(relative.as_posix()):
                            continue  # verification_workspace removes these unconditionally
                        original = (transaction.source_cwd / relative).resolve()
                        try:
                            original.relative_to(transaction.source_cwd)
                            check_path(str(original), "read")
                        except (ValueError, PermissionError, PermissionDeniedError):
                            denied_paths.append(relative.as_posix())
            # A digest is diagnostic binding only. No promotion artifact is
            # created and test side effects never reach the original workspace.
            digest = implementation_candidate_digest(spec, context, snapshot_root=transaction.cwd)
            frozen = transaction.freeze(allowed_paths=spec.write_paths, paths_relative_to_cwd=True)
            evidence = []
            verifier = _deterministic_verifier_for_context(context,
                trusted_git_executable=transaction.trusted_git_executable,
                untrusted_executable_root=str(transaction.source_root))
            with frozen.verification_workspace(read_roots=(".",), forbidden_paths=tuple(denied_paths)) as cwd:
                result = verifier.run(checks=(check,), cwd=str(cwd),
                    workflow_id="wf_direct_implementation", task_id="task_direct_implementation",
                    execution_assignment_id="asg_direct_implementation", patch_digest=digest,
                    permissions=permissions or PermissionManager(context.cwd, prompt=None),
                    permission_cwd=context.cwd, deadline_monotonic=context._task_deadline_monotonic,
                    cancellation_checkpoint=lambda: raise_task_deadline(context),
                    output_store=outputs.store(context), output_sink=capture_output,
                    evidence_sink=lambda _key, document: evidence.append(document))
            return {"checkId": check.check_id, "argv": list(check.argv),
                    "patchDigest": digest, "status": result["status"],
                    "evidence": evidence[0] if len(evidence) == 1 else {},
                    "verificationOutputs": output_manifests,
                    "verification": "implementation_feedback", "writeback": "not_applicable"}

    return ExecutorCheckCapability(spec.checks, run_check, spec.test_paths, spec=spec,
                                   current_patch_digest=lambda context: implementation_candidate_digest(spec, context),
                                   output_reader=output_reader)
