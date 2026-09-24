from __future__ import annotations

import shutil
from pathlib import Path

from minicode.file_review import build_unified_diff
from minicode.tooling import ToolDefinition, ToolContext, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.workspace import relative_display_path, resolve_tool_path


def _operation_checkpoint(context: ToolContext) -> None:
    checkpoint = (
        getattr(context.permissions, "ensure_operation_active", None)
        if context.permissions is not None
        else None
    )
    if callable(checkpoint):
        checkpoint()


def _review_label(context: ToolContext, target: Path) -> str:
    return relative_display_path(target, context.cwd).replace("\\", "/")


def _directory_manifest(path: Path) -> str:
    if not path.exists():
        return ""
    if not path.is_dir():
        return "[file]\n"
    lines = ["[directory]/"]
    for item in sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix()):
        relative = item.relative_to(path).as_posix()
        lines.append(f"{relative}/" if item.is_dir() else relative)
    return "\n".join(lines) + "\n"


def _source_manifest(source: Path) -> str:
    if source.is_dir():
        return _directory_manifest(source)
    return f"[copied file: {source.name}]\n"


def _review_mutation(
    context: ToolContext,
    target: Path,
    *,
    before: str,
    after: str,
) -> None:
    if context.permissions is None:
        return
    label = _review_label(context, target)
    context.permissions.ensure_edit(
        str(target),
        build_unified_diff(label, before, after),
    )


def _error_result(error: BaseException) -> ToolResult:
    return ToolResult(ok=False, output=str(error))


def _unsafe_pair_reason(source: Path, destination: Path) -> str | None:
    """Reject copies/moves whose endpoints overlap.

    The copy path deletes ``destination`` before recreating it, so a pair
    like ``x -> x`` first rmtree()s the source and then fails — destroying
    the tree with nothing left to copy. Destination-inside-source recurses
    into the tree being copied; source-inside-destination copies onto the
    branch the source lives on. Paths arrive already resolved, so pure
    component comparison is sound.
    """
    if source == destination:
        return "source and destination are the same path"
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        return "destination is inside the source directory"
    try:
        source.relative_to(destination)
    except ValueError:
        pass
    else:
        return "source is inside the destination directory"
    return None


def _validate_batch_copy(input_data: dict) -> dict:
    """Validate input for batch_copy tool."""
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    return {"source": source.strip(), "destination": destination.strip()}


def _run_batch_copy(input_data: dict, context: ToolContext) -> ToolResult:
    """Copy files or directories after an explicit destination review."""
    try:
        source = resolve_tool_path(context, input_data["source"], "read")
        destination = resolve_tool_path(context, input_data["destination"], "write")
    except TurnCancellationRequested:
        raise
    except (PermissionError, RuntimeError) as error:
        return _error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    overlap = _unsafe_pair_reason(source, destination)
    if overlap is not None:
        return ToolResult(ok=False, output=f"Copy rejected: {overlap}")

    try:
        _review_mutation(
            context,
            destination,
            before=_directory_manifest(destination),
            after=_source_manifest(source),
        )
        _operation_checkpoint(context)
        if source.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
            return ToolResult(ok=True, output=f"Copied directory to {input_data['destination']}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return ToolResult(ok=True, output=f"Copied file to {input_data['destination']}")
    except TurnCancellationRequested:
        raise
    except (PermissionError, RuntimeError, OSError) as error:
        return ToolResult(ok=False, output=f"Copy failed: {error}")


batch_copy_tool = ToolDefinition(
    name="batch_copy",
    description="Copy files or directories. Supports both files and directories.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source path (relative to workspace)"},
            "destination": {"type": "string", "description": "Destination path (relative to workspace)"}
        },
        "required": ["source", "destination"]
    },
    validator=_validate_batch_copy,
    run=_run_batch_copy,
)


# ---------------------------------------------------------------------------
# Batch Move Tool
# ---------------------------------------------------------------------------

def _validate_batch_move(input_data: dict) -> dict:
    """Validate input for batch_move tool."""
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    return {"source": source.strip(), "destination": destination.strip()}


def _run_batch_move(input_data: dict, context: ToolContext) -> ToolResult:
    """Move only after reviewing both destination write and source deletion."""
    try:
        source = resolve_tool_path(context, input_data["source"], "delete")
        destination = resolve_tool_path(context, input_data["destination"], "write")
    except TurnCancellationRequested:
        raise
    except (PermissionError, RuntimeError) as error:
        return _error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    overlap = _unsafe_pair_reason(source, destination)
    if overlap is not None:
        return ToolResult(ok=False, output=f"Move rejected: {overlap}")

    try:
        source_before = _directory_manifest(source)
        _review_mutation(
            context,
            destination,
            before=_directory_manifest(destination),
            after=_source_manifest(source),
        )
        _review_mutation(
            context,
            source,
            before=source_before,
            after="",
        )
        _operation_checkpoint(context)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return ToolResult(ok=True, output=f"Moved to {input_data['destination']}")
    except TurnCancellationRequested:
        raise
    except (PermissionError, RuntimeError, OSError) as error:
        return ToolResult(ok=False, output=f"Move failed: {error}")


batch_move_tool = ToolDefinition(
    name="batch_move",
    description="Move files or directories to a new location.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source path (relative to workspace)"},
            "destination": {"type": "string", "description": "Destination path (relative to workspace)"}
        },
        "required": ["source", "destination"]
    },
    validator=_validate_batch_move,
    run=_run_batch_move,
)


# ---------------------------------------------------------------------------
# Batch Delete Tool
# ---------------------------------------------------------------------------

def _validate_batch_delete(input_data: dict) -> dict:
    """Validate input for batch_delete tool."""
    path = input_data.get("path", "")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")
    recursive = input_data.get("recursive", False)
    if not isinstance(recursive, bool):
        raise ValueError("recursive must be a boolean")
    return {"path": path.strip(), "recursive": recursive}


def _run_batch_delete(input_data: dict, context: ToolContext) -> ToolResult:
    try:
        target = resolve_tool_path(context, input_data["path"], "delete")
    except TurnCancellationRequested:
        raise
    except (PermissionError, RuntimeError) as error:
        return _error_result(error)
    recursive = input_data.get("recursive", False)

    if not target.exists():
        return ToolResult(ok=False, output=f"Path not found: {input_data['path']}")
    if target.is_dir() and not recursive:
        return ToolResult(ok=False, output="Use recursive=true to delete directories")

    try:
        _review_mutation(
            context,
            target,
            before=_directory_manifest(target),
            after="",
        )
        _operation_checkpoint(context)
        if target.is_dir():
            shutil.rmtree(target)
            return ToolResult(ok=True, output=f"Deleted directory: {input_data['path']}")
        target.unlink()
        return ToolResult(ok=True, output=f"Deleted file: {input_data['path']}")
    except TurnCancellationRequested:
        raise
    except (PermissionError, RuntimeError, OSError) as error:
        return ToolResult(ok=False, output=f"Delete failed: {error}")


batch_delete_tool = ToolDefinition(
    name="batch_delete",
    description="Delete files or directories. Directories require recursive=true.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to delete (relative to workspace)"},
            "recursive": {"type": "boolean", "description": "Required to delete directories"}
        },
        "required": ["path"]
    },
    validator=_validate_batch_delete,
    run=_run_batch_delete,
)
