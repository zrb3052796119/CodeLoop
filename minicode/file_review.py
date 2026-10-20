from __future__ import annotations

import difflib
import os
import stat as stat_module
import tempfile
import unicodedata
from pathlib import Path

from minicode.tooling import ToolContext, ToolResult
from minicode.task_deadline import raise_task_deadline


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


def _atomic_write_text(target: Path, content: str) -> None:
    """Write via same-directory temp file + os.replace.

    A direct write_text can leave a truncated file behind on crash or power
    loss mid-write, destroying the very file being edited. The replace is
    atomic within one directory, and the temp file inherits the target's
    permissions so an edited 0644 file does not silently become 0600.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        try:
            mode = stat_module.S_IMODE(target.stat().st_mode)
        except OSError:
            mode = 0o644
        os.chmod(temp_name, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def apply_reviewed_file_change(
    context: ToolContext,
    file_path: str,
    target_path: str | Path,
    next_content: str,
    *,
    expected_previous: str | None = None,
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

    # Optimistic concurrency: the edit was computed from some earlier read
    # (``expected_previous`` when the caller still holds it, otherwise the
    # read above). If anything — a concurrent sub-agent, an external
    # process — changed the file since, the approved diff no longer
    # describes reality and writing would silently discard that work.
    baseline = previous_content if expected_previous is None else expected_previous
    if load_existing_file(target) != baseline:
        return ToolResult(
            ok=False,
            output=(
                f"error[edit_conflict]: {diff_label} changed while the edit was "
                "being computed or reviewed. Re-read the file and re-apply the "
                "change."
            ),
        )

    candidate_guard = getattr(context, "_implementation_candidate_guard", None)
    if candidate_guard is not None:
        decision = candidate_guard.preflight_write(str(target), next_content)
        if not decision.allowed:
            return ToolResult(ok=False, output=f"error[{decision.code}]: {decision.message}")
        if load_existing_file(target) != baseline:
            return ToolResult(ok=False, output=f"error[edit_conflict]: {diff_label} changed during candidate protection.")
    raise_task_deadline(context)
    _atomic_write_text(target, next_content)
    return ToolResult(ok=True, output=f"Applied reviewed changes to {diff_label}", _file_changed=True)
