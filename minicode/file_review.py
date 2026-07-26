from __future__ import annotations

import difflib
import unicodedata
from pathlib import Path

from minicode.tooling import ToolContext, ToolResult


_UNSAFE_DIFF_LABEL_MESSAGE = "File review target is not a safe workspace-local path."
_REDACTED_SENSITIVE_REVIEW = "[REDACTED SENSITIVE REVIEW]"


def contains_unsafe_review_character(value: str) -> bool:
    """Reject text controls before any line splitting or UTF-8 encoding.

    LF and tab are ordinary review structure. CR is accepted only as the first
    half of CRLF so existing cross-platform file input remains compatible.
    Other control, format, and surrogate code points cannot be represented
    faithfully and safely in approval surfaces.
    """
    for index, character in enumerate(value):
        if character in {"\n", "\t"}:
            continue
        if character == "\r":
            if index + 1 < len(value) and value[index + 1] == "\n":
                continue
            return True
        codepoint = ord(character)
        if (
            character in {"\u2028", "\u2029", "\ufeff"}
            or 0x200B <= codepoint <= 0x200F
            or 0x202A <= codepoint <= 0x202E
            or 0x2060 <= codepoint <= 0x206F
        ):
            return True
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
            return True
    return False


def _validate_diff_label(label: str) -> str:
    if (
        not label
        or label.startswith("/")
        or "file://" in label.casefold()
        or any(part in {"", ".", ".."} for part in label.split("/"))
        or any(unicodedata.category(character).startswith("C") for character in label)
    ):
        raise PermissionError(_UNSAFE_DIFF_LABEL_MESSAGE)
    return label


def _workspace_relative_diff_target(
    context: ToolContext,
    target_path: str | Path,
) -> tuple[Path, str]:
    try:
        workspace = Path(context.cwd).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("workspace is not a directory")
        target = Path(target_path).expanduser().resolve(strict=False)
        relative = target.relative_to(workspace)
    except (OSError, RuntimeError, ValueError) as error:
        raise PermissionError(_UNSAFE_DIFF_LABEL_MESSAGE) from error
    return target, _validate_diff_label(relative.as_posix())


def build_unified_diff(file_path: str, before: str, after: str) -> str:
    file_path = _validate_diff_label(file_path)
    if before == after:
        return f"(no changes for {file_path})"
    if contains_unsafe_review_character(before) or contains_unsafe_review_character(
        after
    ):
        return _REDACTED_SENSITIVE_REVIEW
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
    target, diff_label = _workspace_relative_diff_target(context, target_path)
    previous_content = load_existing_file(target)
    if previous_content == next_content:
        return ToolResult(ok=True, output=f"No changes needed for {diff_label}")

    diff = build_unified_diff(diff_label, previous_content, next_content)
    if context.permissions is not None:
        context.permissions.ensure_edit(str(target), diff)
        checkpoint = getattr(context.permissions, "ensure_operation_active", None)
        if checkpoint is not None:
            checkpoint()

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(next_content, encoding="utf-8")
    return ToolResult(ok=True, output=f"Applied reviewed changes to {diff_label}")
