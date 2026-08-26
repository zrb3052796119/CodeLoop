"""Executor capabilities are enforced by Assignment paths, not tool names."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.permissions import NetworkPermissionError, PermissionDeniedError
from minicode.scheduler_permissions import (
    MANDATORY_SCHEDULER_FORBIDDEN_PATHS,
    SchedulerExecutorPermissions,
    SchedulerWorkerReadPermissions,
    is_mandatory_scheduler_forbidden_path,
    mandatory_scheduler_forbidden_paths,
    scheduler_paths_overlap,
)


def _permissions(tmp_path: Path) -> SchedulerExecutorPermissions:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / ".git").mkdir()
    return SchedulerExecutorPermissions(
        str(tmp_path),
        read_roots=(".",),
        write_paths=("src/allowed.py",),
        forbidden_paths=(".git",),
    )


def test_executor_allows_only_scheduler_owned_write_paths(tmp_path: Path) -> None:
    permissions = _permissions(tmp_path)

    permissions.ensure_path_access(str(tmp_path / "src" / "allowed.py"), "write")
    permissions.ensure_edit(str(tmp_path / "src" / "allowed.py"), "diff")
    permissions.ensure_path_access(str(tmp_path / "docs"), "read")

    with pytest.raises(PermissionDeniedError, match="Assignment scope"):
        permissions.ensure_path_access(str(tmp_path / "src" / "other.py"), "write")
    with pytest.raises(PermissionDeniedError, match="forbidden"):
        permissions.ensure_path_access(str(tmp_path / ".git" / "config"), "read")
    with pytest.raises(PermissionDeniedError, match="isolated workspace"):
        permissions.ensure_path_access(str(tmp_path.parent / "outside.py"), "read")


def test_executor_cannot_run_commands_or_use_network(tmp_path: Path) -> None:
    permissions = _permissions(tmp_path)

    with pytest.raises(PermissionDeniedError, match="Deterministic Verifier"):
        permissions.ensure_command("find", ["/tmp", "-delete"], str(tmp_path))
    with pytest.raises(NetworkPermissionError):
        permissions.ensure_network({})


def test_scheduler_forbidden_helpers_are_closed_normalized_and_reusable() -> None:
    merged = mandatory_scheduler_forbidden_paths(("docs/private", ".git"))

    assert merged[: len(MANDATORY_SCHEDULER_FORBIDDEN_PATHS)] == (
        MANDATORY_SCHEDULER_FORBIDDEN_PATHS
    )
    assert merged.count(".git") == 1
    assert merged[-1] == "docs/private"
    assert scheduler_paths_overlap("src", "src/auth/token.py") is True
    assert scheduler_paths_overlap("src/auth.py", "tests/auth.py") is False
    assert is_mandatory_scheduler_forbidden_path("src/.env.local") is True
    assert is_mandatory_scheduler_forbidden_path(".mini-code-memory/project_facts.json")
    assert is_mandatory_scheduler_forbidden_path("src/credentials.py") is False

    with pytest.raises(ValueError, match="unsafe"):
        scheduler_paths_overlap("../outside", "src")


def test_read_worker_allows_only_declared_read_roots_and_declared_safe_reads(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "private").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    permissions = SchedulerWorkerReadPermissions(
        str(tmp_path),
        read_roots=("src",),
        forbidden_paths=("src/private",),
    )

    permissions.ensure_path_access(str(tmp_path / "src" / "public.py"), "read")
    permissions.ensure_path_access(str(tmp_path / "src"), "list")

    with pytest.raises(PermissionDeniedError, match="read scope"):
        permissions.ensure_path_access(str(tmp_path / "docs"), "read")
    with pytest.raises(PermissionDeniedError, match="forbidden"):
        permissions.ensure_path_access(
            str(tmp_path / "src" / "private" / "note.txt"),
            "read",
        )
    with pytest.raises(PermissionDeniedError, match="workspace"):
        permissions.ensure_path_access(str(tmp_path.parent / "outside.py"), "read")


@pytest.mark.parametrize(
    "relative_path",
    [
        ".git/config",
        ".env",
        ".envrc",
        "config/.env-secret",
        "config/.env.local",
        ".git-credentials",
        ".netrc",
        "credentials/service-account.json",
        "config/service-account.json",
        "config/service_account.json",
        "keys/id_rsa",
        "keys/signing.key",
        "certs/client.pem",
        "certs/client.p12",
        "credentials.json",
        "config/credentials.yaml",
        "config/secrets.toml",
        "secrets/token.txt",
        ".ssh/id_ed25519",
        ".aws/credentials",
        ".mini-code/settings.json",
        ".mini-code-memory/project_facts.json",
        ".mini-code-memory-local/memory.json",
        ".mini-code-session-memory/session.json",
        ".mini-code-tool-results/result.json",
        ".codex/state.json",
        ".claude/settings.json",
        ".agents/state.json",
    ],
)
def test_read_worker_enforces_mandatory_forbidden_paths_even_when_undeclared(
    tmp_path: Path,
    relative_path: str,
) -> None:
    permissions = SchedulerWorkerReadPermissions(
        str(tmp_path),
        read_roots=(".",),
        forbidden_paths=(),
    )

    with pytest.raises(PermissionDeniedError, match="mandatory forbidden"):
        permissions.ensure_path_access(str(tmp_path / relative_path), "read")


@pytest.mark.parametrize("intent", ["write", "delete"])
def test_read_worker_denies_all_mutation_command_and_network_capabilities(
    tmp_path: Path,
    intent: str,
) -> None:
    permissions = SchedulerWorkerReadPermissions(
        str(tmp_path),
        read_roots=(".",),
        forbidden_paths=(),
    )
    target = str(tmp_path / "allowed.py")

    with pytest.raises(PermissionDeniedError, match="read-only"):
        permissions.ensure_path_access(target, intent)
    with pytest.raises(PermissionDeniedError, match="read-only"):
        permissions.ensure_edit(target, "diff")
    with pytest.raises(PermissionDeniedError, match="read-only"):
        permissions.ensure_command("git", ["status"], str(tmp_path))
    with pytest.raises(NetworkPermissionError):
        permissions.ensure_network({})


def test_read_worker_preserves_the_parent_operation_checkpoint(tmp_path: Path) -> None:
    checkpoints: list[str] = []
    permissions = SchedulerWorkerReadPermissions(
        str(tmp_path),
        read_roots=(".",),
        forbidden_paths=(),
        operation_checkpoint=lambda: checkpoints.append("checked"),
    )

    permissions.ensure_operation_active()

    assert checkpoints == ["checked"]
    assert permissions.prompt is None


def test_executor_also_enforces_mandatory_forbidden_paths(tmp_path: Path) -> None:
    permissions = _permissions(tmp_path)

    with pytest.raises(PermissionDeniedError, match="mandatory forbidden"):
        permissions.ensure_path_access(str(tmp_path / ".env.local"), "read")
