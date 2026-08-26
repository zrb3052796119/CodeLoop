"""Fail-closed path capabilities for centrally scheduled Workers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from minicode.permissions import NetworkPermissionError, PermissionDeniedError


_WRITE_INTENTS = frozenset({"write", "delete"})
_EXECUTION_INTENTS = frozenset({"command", "command_cwd", "execute", "test"})

# These locations never become Worker-readable merely because an Assignment
# declares a broad root such as ``.``. The list covers repository metadata,
# environment/credential stores, and MiniCode/Codex orchestration state.
MANDATORY_SCHEDULER_FORBIDDEN_PATHS: tuple[str, ...] = (
    ".git",
    ".env",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    ".mcp.json",
    ".ssh",
    ".aws",
    ".azure",
    ".kube",
    ".gnupg",
    ".docker",
    ".mini-code",
    ".mini-code-memory",
    ".mini-code-memory-local",
    ".mini-code-session-memory",
    ".mini-code-tool-results",
    ".codex",
    ".claude",
    ".agents",
)

_MANDATORY_FORBIDDEN_SEGMENTS = frozenset(
    path.casefold() for path in MANDATORY_SCHEDULER_FORBIDDEN_PATHS if "/" not in path
)
_CREDENTIAL_DATA_FILES = frozenset(
    {
        "auth.json",
        "client-secret.json",
        "client_secret.json",
        "credentials.ini",
        "credentials.toml",
        "credentials.yaml",
        "credentials.yml",
        "google-credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "service-account.json",
        "service_account.json",
        "secrets.ini",
        "secrets.toml",
        "secrets.yaml",
        "secrets.yml",
        ".terraformrc",
        ".vault-token",
    }
)
_CREDENTIAL_DATA_SUFFIXES = (
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
)


def _normalize_scheduler_path(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("scheduler scope contains an unsafe path")
    normalized_separators = value.replace("\\", "/")
    if (
        PurePosixPath(normalized_separators).is_absolute()
        or PureWindowsPath(normalized_separators).is_absolute()
    ):
        raise ValueError("scheduler scope contains an unsafe path")
    parts = [part for part in normalized_separators.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise ValueError("scheduler scope contains an unsafe path")
    return "/".join(parts) or "."


def scheduler_paths_overlap(left: str, right: str) -> bool:
    """Return whether two safe workspace-relative paths contain each other."""

    normalized_left = _normalize_scheduler_path(left).casefold()
    normalized_right = _normalize_scheduler_path(right).casefold()
    return bool(
        normalized_left == "."
        or normalized_right == "."
        or normalized_left == normalized_right
        or normalized_left.startswith(normalized_right.rstrip("/") + "/")
        or normalized_right.startswith(normalized_left.rstrip("/") + "/")
    )


def is_mandatory_scheduler_forbidden_path(path: str) -> bool:
    """Return whether a relative path enters credentials or runtime state."""

    normalized = _normalize_scheduler_path(path)
    parts = tuple(part.casefold() for part in PurePosixPath(normalized).parts)
    return any(
        part in _MANDATORY_FORBIDDEN_SEGMENTS
        or part.startswith(".env")
        or part in _CREDENTIAL_DATA_FILES
        or part.endswith(_CREDENTIAL_DATA_SUFFIXES)
        for part in parts
    )


def mandatory_scheduler_forbidden_paths(
    declared_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Merge caller-declared paths with the immutable mandatory deny set."""

    if not isinstance(declared_paths, tuple):
        raise ValueError("scheduler forbidden_paths must be a tuple")
    merged = list(MANDATORY_SCHEDULER_FORBIDDEN_PATHS)
    seen = {path.casefold() for path in merged}
    for raw_path in declared_paths:
        path = _normalize_scheduler_path(raw_path)
        key = path.casefold()
        if key not in seen:
            merged.append(path)
            seen.add(key)
    return tuple(merged)


class _SchedulerScopedPermissions:
    """Shared filesystem containment and cancellation checkpoint behavior."""

    def __init__(
        self,
        workspace_root: str,
        *,
        read_roots: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
        operation_checkpoint: Callable[[], None] | None,
        subject: str,
    ) -> None:
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("scheduler workspace_root must be a directory")
        if operation_checkpoint is not None and not callable(operation_checkpoint):
            raise TypeError("operation_checkpoint must be callable or null")
        self.workspace_root = str(root)
        self.prompt = None
        self.operation_checkpoint = operation_checkpoint
        self._root = root
        self._subject = subject
        self._read_roots = self._resolve_scope(
            read_roots,
            label="read_roots",
        )
        self._declared_forbidden_paths = self._resolve_scope(
            forbidden_paths,
            label="forbidden_paths",
        )
        self._mandatory_forbidden_paths = self._resolve_scope(
            MANDATORY_SCHEDULER_FORBIDDEN_PATHS,
            label="mandatory forbidden paths",
        )
        if not self._read_roots:
            raise ValueError("scheduler read_roots must be non-empty")

    def _resolve_scope(
        self,
        values: tuple[str, ...],
        *,
        label: str,
    ) -> tuple[Path, ...]:
        if not isinstance(values, tuple):
            raise ValueError(f"scheduler {label} must be a unique tuple")
        resolved: list[Path] = []
        seen: set[str] = set()
        for value in values:
            normalized = _normalize_scheduler_path(value)
            target = (self._root / normalized).resolve(strict=False)
            if not self._contains(self._root, target):
                raise ValueError(f"scheduler {label} escapes the workspace")
            key = str(target).casefold()
            if key in seen:
                raise ValueError(f"scheduler {label} must be a unique tuple")
            seen.add(key)
            resolved.append(target)
        return tuple(resolved)

    @staticmethod
    def _contains(root: Path, target: Path) -> bool:
        try:
            target.relative_to(root)
        except ValueError:
            return False
        return True

    @classmethod
    def _within_any(cls, target: Path, roots: tuple[Path, ...]) -> bool:
        return any(cls._contains(root, target) for root in roots)

    def _target(self, target_path: str) -> Path:
        if not isinstance(target_path, str) or not target_path:
            raise PermissionDeniedError(
                f"{self._subject} path is invalid",
                code="path_assignment_scope_denied",
            )
        try:
            candidate = Path(target_path)
            target = (
                candidate if candidate.is_absolute() else self._root / candidate
            ).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PermissionDeniedError(
                f"{self._subject} path is invalid",
                code="path_assignment_scope_denied",
            ) from exc
        if not self._contains(self._root, target):
            raise PermissionDeniedError(
                f"{self._subject} path is outside its isolated workspace",
                code="path_assignment_scope_denied",
            )
        relative = target.relative_to(self._root).as_posix() or "."
        if is_mandatory_scheduler_forbidden_path(relative) or self._within_any(
            target,
            self._mandatory_forbidden_paths,
        ):
            raise PermissionDeniedError(
                f"{self._subject} path is mandatory forbidden",
                code="path_scheduler_mandatory_forbidden",
            )
        if self._within_any(target, self._declared_forbidden_paths):
            raise PermissionDeniedError(
                f"{self._subject} path is forbidden by its Assignment",
                code="path_assignment_forbidden",
            )
        return target

    def ensure_network(self, _review: dict[str, object]) -> str:
        raise NetworkPermissionError("permission_denied")

    def ensure_operation_active(self) -> None:
        if self.operation_checkpoint is not None:
            self.operation_checkpoint()

    def begin_turn(self) -> None:
        return None

    def end_turn(self) -> None:
        return None


class SchedulerWorkerReadPermissions(_SchedulerScopedPermissions):
    """Read-only runtime capabilities shared by Analyst and Reviewer Workers."""

    def __init__(
        self,
        workspace_root: str,
        *,
        read_roots: tuple[str, ...],
        forbidden_paths: tuple[str, ...] = (),
        operation_checkpoint: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            workspace_root,
            read_roots=read_roots,
            forbidden_paths=forbidden_paths,
            operation_checkpoint=operation_checkpoint,
            subject="Scheduler Worker",
        )

    def ensure_path_access(self, target_path: str, intent: str) -> None:
        target = self._target(target_path)
        if intent in _WRITE_INTENTS:
            raise PermissionDeniedError(
                "Scheduler Worker is read-only",
                code="edit_scheduler_worker_read_only",
            )
        if intent in _EXECUTION_INTENTS:
            raise PermissionDeniedError(
                "Scheduler Worker is read-only and cannot execute commands",
                code="command_scheduler_worker_denied",
            )
        if not self._within_any(target, self._read_roots):
            raise PermissionDeniedError(
                "Scheduler Worker path exceeds its read scope",
                code="path_assignment_scope_denied",
            )

    def ensure_edit(self, _target_path: str, _diff_preview: str) -> None:
        raise PermissionDeniedError(
            "Scheduler Worker is read-only",
            code="edit_scheduler_worker_read_only",
        )

    def ensure_command(
        self,
        _command: str,
        _args: list[str],
        _command_cwd: str,
        force_prompt_reason: str | None = None,
    ) -> None:
        del force_prompt_reason
        raise PermissionDeniedError(
            "Scheduler Worker is read-only and cannot run commands",
            code="command_scheduler_worker_denied",
        )

    def get_summary(self) -> list[str]:
        return [
            f"scheduler worker workspace: {self.workspace_root}",
            "scheduler worker commands/network/writes: denied",
            "scheduler worker reads: assignment-scoped",
        ]


class SchedulerExecutorPermissions(_SchedulerScopedPermissions):
    """Allow an Executor to write only scheduler-owned target paths."""

    def __init__(
        self,
        workspace_root: str,
        *,
        read_roots: tuple[str, ...],
        write_paths: tuple[str, ...],
        forbidden_paths: tuple[str, ...],
        operation_checkpoint: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            workspace_root,
            read_roots=read_roots,
            forbidden_paths=forbidden_paths,
            operation_checkpoint=operation_checkpoint,
            subject="Executor",
        )
        self._write_paths = self._resolve_scope(
            write_paths,
            label="write_paths",
        )

    def ensure_path_access(self, target_path: str, intent: str) -> None:
        target = self._target(target_path)
        roots = self._write_paths if intent in _WRITE_INTENTS else self._read_roots
        if not self._within_any(target, roots):
            raise PermissionDeniedError(
                "Executor path exceeds its Assignment scope",
                code=(
                    "edit_assignment_scope_denied"
                    if intent in _WRITE_INTENTS
                    else "path_assignment_scope_denied"
                ),
            )

    def ensure_edit(self, target_path: str, _diff_preview: str) -> None:
        self.ensure_path_access(target_path, "write")

    def ensure_command(
        self,
        _command: str,
        _args: list[str],
        _command_cwd: str,
        force_prompt_reason: str | None = None,
    ) -> None:
        del force_prompt_reason
        raise PermissionDeniedError(
            "Executor commands are reserved for the Deterministic Verifier",
            code="command_executor_denied",
        )

    def get_summary(self) -> list[str]:
        return [
            f"executor workspace: {self.workspace_root}",
            "executor commands/network: denied",
            "executor writes: scheduler-scoped",
        ]


__all__ = [
    "MANDATORY_SCHEDULER_FORBIDDEN_PATHS",
    "SchedulerExecutorPermissions",
    "SchedulerWorkerReadPermissions",
    "is_mandatory_scheduler_forbidden_path",
    "mandatory_scheduler_forbidden_paths",
    "scheduler_paths_overlap",
]
