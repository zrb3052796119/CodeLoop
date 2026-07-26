from __future__ import annotations

import difflib
import os
import tempfile
from pathlib import Path

from minicode.tooling import ToolContext, ToolResult


def build_unified_diff(file_path: str, before: str, after: str) -> str:
    if before == after:
        return f"(no changes for {file_path})"
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
        n=3,
    )
    # Strip redundant separator lines (e.g. "=" lines) for compact display
    lines = [line for line in diff if not (line.startswith("=") and set(line.strip()) == {"="})]
    return "\n".join(lines)


def load_existing_file(target_path: str | Path) -> str:
    file_path = Path(target_path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def apply_reviewed_file_change(
    context: ToolContext,
    file_path: str,
    target_path: str | Path,
    next_content: str,
) -> ToolResult:
    target = Path(target_path)
    previous_content = load_existing_file(target)
    if previous_content == next_content:
        return ToolResult(ok=True, output=f"No changes needed for {file_path}")

    diff = build_unified_diff(file_path, previous_content, next_content)
    if context.permissions is not None:
        context.permissions.ensure_edit(str(target), diff)

    target.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file then rename to avoid corruption
    fd, tmp_path = tempfile.mkstemp(
        dir=target.parent,
        suffix=".tmp",
        prefix=target.name + ".",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(next_content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return ToolResult(ok=True, output=f"Applied reviewed changes to {file_path}")
