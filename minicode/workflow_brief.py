"""Complete workflow text carried by a permission-checked, digest-bound file."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import tempfile

from minicode.multi_agent_contracts import parse_workflow_request
from minicode.scheduler_permissions import SchedulerWorkerReadPermissions
from minicode.scheduler_path_policy import normalized_path_has_windows_ambiguous_component
from minicode.tooling import ToolContext
from minicode.workspace import resolve_tool_path


WORKFLOW_BRIEF_SUMMARY_MAX_CHARS = 800
_STORE = ".workflow-briefs"


@dataclass(frozen=True)
class WorkflowBrief:
    source_path: str
    path: str
    sha256: str
    body: str
    summary: str

    def render_prompt(self) -> str:
        return (
            f"{self.summary}\n\n"
            f"Read the complete task brief at `{self.path}` before working. "
            "Its requirements and context remain authoritative in full; "
            "use read_file paging until the complete brief is read.\n"
            f"SHA-256: {self.sha256}"
        ).strip()


def validate_workflow_brief_prompt(prompt: str) -> tuple[str, str] | None:
    """Recognize the existing prompt field's file convention without I/O."""
    if not prompt.startswith("@file:"):
        return None
    first_line, _, summary = prompt.partition("\n")
    path = first_line.removeprefix("@file:").strip().replace("\\", "/")
    if (
        not path
        or len(path) > 500
        or "\x00" in path
        or PurePosixPath(path).is_absolute()
        or PureWindowsPath(path).drive
        or ".." in PurePosixPath(path).parts
        or normalized_path_has_windows_ambiguous_component(path)
    ):
        raise ValueError("workflow brief must name a safe workspace-relative file")
    summary = summary.strip()
    if len(summary) > WORKFLOW_BRIEF_SUMMARY_MAX_CHARS:
        raise ValueError("workflow brief inline summary exceeds 800 characters")
    return path, summary


def write_workflow_artifact(context: ToolContext, body: bytes) -> tuple[str, str]:
    """Atomically persist exact bytes; never silently truncate file contents."""
    digest = hashlib.sha256(body).hexdigest()
    relative = f"{_STORE}/{digest}.md"
    root = Path(context.cwd).resolve(strict=True)
    directory = root / _STORE
    if directory.is_symlink():
        raise PermissionError("workflow brief store cannot be a symlink")
    target = resolve_tool_path(context, relative, "write")
    if target != directory / f"{digest}.md":
        raise PermissionError("workflow brief store path was redirected")
    directory.mkdir(mode=0o700, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".pending-", delete=False) as handle:
            temporary = handle.name
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return relative, digest


def prepare_workflow_brief(input_data: dict, context: ToolContext) -> WorkflowBrief | None:
    """Authorize the source before reading; persist it before snapshot creation."""
    parsed = validate_workflow_brief_prompt(input_data["prompt"])
    if parsed is None:
        return None
    source_path, summary = parsed
    request = parse_workflow_request(
        input_data.get("workflow_request"), legacy_task_id="task_workflow_brief"
    )
    source = resolve_tool_path(context, source_path, "read")
    root = Path(context.cwd).resolve(strict=True)
    if not source.is_relative_to(root):
        raise PermissionError("workflow brief must remain inside the workspace")
    SchedulerWorkerReadPermissions(
        str(root), read_roots=request.scope.read_roots,
        forbidden_paths=request.scope.forbidden_paths,
    ).ensure_path_access(str(source), "read")
    if not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("workflow brief must be a regular file")
    raw = source.read_bytes()
    body = raw.decode("utf-8")
    if not body.strip() or "\x00" in body:
        raise ValueError("workflow brief must contain nonempty UTF-8 text without NUL")
    path, digest = write_workflow_artifact(context, raw)
    SchedulerWorkerReadPermissions(
        str(root), read_roots=(path,), forbidden_paths=request.scope.forbidden_paths,
    ).ensure_path_access(str(root / path), "read")
    return WorkflowBrief(source_path, path, digest, body, summary)


def verify_workflow_brief(brief: WorkflowBrief, workspace: Path | str) -> None:
    """Check the copied snapshot bytes before granting the exact file read path."""
    if not isinstance(brief, WorkflowBrief):
        raise ValueError("workflow brief was not prepared by the host")
    root = Path(workspace).resolve(strict=True)
    expected = f"{_STORE}/{brief.sha256}.md"
    if (
        brief.path != expected
        or len(brief.sha256) != 64
        or any(char not in "0123456789abcdef" for char in brief.sha256)
    ):
        raise ValueError("workflow brief reference is invalid")
    target = root / brief.path
    if target.is_symlink() or target.parent.is_symlink() or target.resolve() != target:
        raise PermissionError("workflow brief reference was redirected")
    if hashlib.sha256(target.read_bytes()).hexdigest() != brief.sha256:
        raise ValueError("workflow brief digest mismatch")


__all__ = [
    "WorkflowBrief", "WORKFLOW_BRIEF_SUMMARY_MAX_CHARS",
    "validate_workflow_brief_prompt", "prepare_workflow_brief",
    "verify_workflow_brief", "write_workflow_artifact",
]
