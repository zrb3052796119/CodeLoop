from __future__ import annotations

from pathlib import Path

from minicode.tooling import ToolContext


INTERNAL_WORKSPACE_STORE_NAMES = frozenset(
    {".mini-code-memory", ".mini-code-memory-local"}
)


def is_internal_workspace_path(path: Path | str, workspace: Path | str) -> bool:
    """Return whether a resolved path enters a model-inaccessible store."""
    resolved = Path(path).resolve(strict=False)
    root = Path(workspace).resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return False
    return any(part in INTERNAL_WORKSPACE_STORE_NAMES for part in relative.parts)


def resolve_tool_path(context: ToolContext, input_path: str, intent: str) -> Path:
    candidate = Path(input_path)
    target = candidate if candidate.is_absolute() else Path(context.cwd) / candidate
    normalized = target.resolve()

    if is_internal_workspace_path(normalized, context.cwd):
        raise PermissionError(
            "Path targets MiniCode internal Memory state and is not available "
            "to model tools."
        )

    if context.permissions is not None:
        context.permissions.ensure_path_access(str(normalized), intent)
        checkpoint = getattr(context.permissions, "ensure_operation_active", None)
        if checkpoint is not None:
            checkpoint()
    else:
        # Fallback: block paths that escape the workspace when no permissions manager
        workspace_root = Path(context.cwd).resolve()
        try:
            normalized.relative_to(workspace_root)
        except ValueError:
            raise PermissionError(f"Path escapes workspace: {input_path}")

    return normalized


def relative_display_path(path: Path | str, cwd: str | Path) -> str:
    """Best-effort workspace-relative display path.

    ``resolve_tool_path`` resolves symlinks (macOS ``/var`` → ``/private/var``),
    so a resolved file path is often not ``relative_to`` the unresolved cwd.
    Try both bases and fall back to the absolute path instead of raising.
    """
    target = Path(path)
    for base in (Path(cwd), Path(cwd).resolve()):
        try:
            return str(target.relative_to(base))
        except ValueError:
            continue
    return str(target)
