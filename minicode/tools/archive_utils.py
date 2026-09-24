from __future__ import annotations

from dataclasses import dataclass
import gzip
import os
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import BinaryIO

from minicode.permissions import PermissionDeniedError
from minicode.task_deadline import TaskDeadlineExceeded, raise_task_deadline
from minicode.tooling import ToolDefinition, ToolContext, ToolResult
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.workspace import relative_display_path, resolve_tool_path


MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
ARCHIVE_READ_CHUNK_BYTES = 64 * 1024
ARCHIVE_EXTRACTION_TIMEOUT_SECONDS = 30.0


class ArchiveSafetyError(ValueError):
    """A bounded, user-actionable archive rejection."""


@dataclass(slots=True)
class _ExtractionBudget:
    context: ToolContext
    deadline: float
    members: int = 0
    declared_bytes: int = 0
    written_bytes: int = 0

    def checkpoint(self) -> None:
        raise_task_deadline(self.context)
        if time.monotonic() >= self.deadline:
            raise ArchiveSafetyError("Archive extraction deadline exceeded")
        _operation_checkpoint(self.context)

    def register_member(self, declared_size: int) -> None:
        self.checkpoint()
        if declared_size < 0:
            raise ArchiveSafetyError("Archive member has an invalid size")
        self.members += 1
        if self.members > MAX_ARCHIVE_MEMBERS:
            raise ArchiveSafetyError(
                f"Archive exceeds the {MAX_ARCHIVE_MEMBERS} member limit"
            )
        self.declared_bytes += declared_size
        if self.declared_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArchiveSafetyError(
                "Archive exceeds the aggregate uncompressed byte limit"
            )

    def account_written(self, size: int) -> None:
        self.written_bytes += size
        if self.written_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArchiveSafetyError(
                "Archive exceeds the aggregate uncompressed byte limit"
            )


def _operation_checkpoint(context: ToolContext) -> None:
    permissions = context.permissions
    checkpoint = (
        getattr(permissions, "ensure_operation_active", None)
        if permissions is not None
        else None
    )
    if callable(checkpoint):
        checkpoint()


def _mutation_checkpoint(context: ToolContext) -> None:
    """Check task/Turn authority immediately before or during archive I/O."""
    raise_task_deadline(context)
    _operation_checkpoint(context)


def _extraction_deadline(context: ToolContext) -> float:
    deadline = time.monotonic() + ARCHIVE_EXTRACTION_TIMEOUT_SECONDS
    task_deadline = context._task_deadline_monotonic
    if isinstance(task_deadline, (int, float)):
        deadline = min(deadline, float(task_deadline))
    return deadline


def _resolve_archive_member(destination: Path, member_name: str) -> Path:
    normalized_name = member_name.replace("\\", "/")
    member_path = Path(normalized_name)
    if member_path.is_absolute() or any(part == ".." for part in member_path.parts):
        raise ArchiveSafetyError(
            f"Archive member escapes extraction destination: {member_name}"
        )

    destination_root = destination.resolve(strict=False)
    target = (destination_root / member_path).resolve(strict=False)
    try:
        target.relative_to(destination_root)
    except ValueError as error:
        raise ArchiveSafetyError(
            f"Archive member escapes extraction destination: {member_name}"
        ) from error
    return target


def _copy_bounded(source: BinaryIO, destination: BinaryIO, budget: _ExtractionBudget) -> None:
    while True:
        budget.checkpoint()
        remaining = MAX_ARCHIVE_UNCOMPRESSED_BYTES - budget.written_bytes
        chunk = source.read(min(ARCHIVE_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            return
        budget.account_written(len(chunk))
        destination.write(chunk)


def _copy_with_checkpoints(
    source: BinaryIO,
    destination: BinaryIO,
    context: ToolContext,
    *,
    deadline: float | None = None,
) -> None:
    while True:
        _mutation_checkpoint(context)
        if deadline is not None and time.monotonic() >= deadline:
            raise ArchiveSafetyError("Archive extraction deadline exceeded")
        chunk = source.read(ARCHIVE_READ_CHUNK_BYTES)
        if not chunk:
            return
        destination.write(chunk)


def _make_staging_directory(destination: Path, context: ToolContext) -> Path:
    _mutation_checkpoint(context)
    if destination.exists():
        if not destination.is_dir():
            raise ArchiveSafetyError("Extraction destination is not a directory")
        staging_parent = destination
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_parent = destination.parent
    return Path(tempfile.mkdtemp(prefix=".minicode-archive-", dir=staging_parent))


def _ensure_publish_parent(path: Path, created_directories: list[Path]) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise ArchiveSafetyError("Archive member parent is not a directory")
    for directory in reversed(missing):
        directory.mkdir()
        created_directories.append(directory)


def _publish_staging_directory(
    staging: Path,
    destination: Path,
    context: ToolContext,
    *,
    deadline: float,
) -> None:
    """Publish a fully bounded extraction and roll back an interrupted merge."""
    _mutation_checkpoint(context)
    if time.monotonic() >= deadline:
        raise ArchiveSafetyError("Archive extraction deadline exceeded")
    if not destination.exists():
        staging.replace(destination)
        return
    if not destination.is_dir():
        raise ArchiveSafetyError("Extraction destination is not a directory")

    entries = sorted(
        staging.rglob("*"),
        key=lambda item: (len(item.relative_to(staging).parts), item.as_posix()),
    )
    rollback_root = staging / ".rollback"
    rollback_root.mkdir()
    created_files: list[Path] = []
    created_directories: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    try:
        for item in entries:
            relative = item.relative_to(staging)
            target = _resolve_archive_member(destination, relative.as_posix())
            _mutation_checkpoint(context)
            if time.monotonic() >= deadline:
                raise ArchiveSafetyError("Archive extraction deadline exceeded")
            if item.is_dir():
                if target.exists():
                    if not target.is_dir():
                        raise ArchiveSafetyError(
                            "Archive directory conflicts with an existing file"
                        )
                else:
                    _ensure_publish_parent(target.parent, created_directories)
                    target.mkdir()
                    created_directories.append(target)
                continue

            _ensure_publish_parent(target.parent, created_directories)
            if target.exists():
                if not target.is_file():
                    raise ArchiveSafetyError(
                        "Archive file conflicts with a non-regular destination"
                    )
                backup = rollback_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
                backups.append((target, backup))
            else:
                created_files.append(target)
            with open(item, "rb") as source, open(target, "wb") as output:
                _copy_with_checkpoints(
                    source,
                    output,
                    context,
                    deadline=deadline,
                )
    except BaseException:
        for target in reversed(created_files):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        for target, backup in reversed(backups):
            try:
                target.unlink(missing_ok=True)
                backup.replace(target)
            except OSError:
                pass
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _safe_extract_zip(
    source: Path,
    destination: Path,
    context: ToolContext,
) -> None:
    budget = _ExtractionBudget(context, _extraction_deadline(context))
    with zipfile.ZipFile(source, "r") as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ArchiveSafetyError(
                f"Archive exceeds the {MAX_ARCHIVE_MEMBERS} member limit"
            )
        for info in members:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ArchiveSafetyError(
                    f"Archive member uses unsupported link: {info.filename}"
                )
            if info.flag_bits & 0x1:
                raise ArchiveSafetyError(
                    f"Archive member is encrypted: {info.filename}"
                )
            _resolve_archive_member(destination, info.filename)
            budget.register_member(0 if info.is_dir() else info.file_size)

        staging = _make_staging_directory(destination, context)
        try:
            for info in members:
                target = _resolve_archive_member(staging, info.filename)
                budget.checkpoint()
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as member_source, open(target, "wb") as output:
                    _copy_bounded(member_source, output, budget)
            _publish_staging_directory(
                staging,
                destination,
                context,
                deadline=budget.deadline,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _safe_extract_tar(
    source: Path,
    destination: Path,
    context: ToolContext,
) -> None:
    budget = _ExtractionBudget(context, _extraction_deadline(context))
    with tarfile.open(source, "r:*") as archive:
        members: list[tarfile.TarInfo] = []
        for member in archive:
            if member.issym() or member.islnk():
                raise ArchiveSafetyError(
                    f"Archive member uses unsupported link: {member.name}"
                )
            _resolve_archive_member(destination, member.name)
            budget.register_member(member.size if member.isfile() else 0)
            members.append(member)

        staging = _make_staging_directory(destination, context)
        try:
            for member in members:
                target = _resolve_archive_member(staging, member.name)
                budget.checkpoint()
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                member_source = archive.extractfile(member)
                if member_source is None:
                    continue
                with member_source, open(target, "wb") as output:
                    _copy_bounded(member_source, output, budget)
            _publish_staging_directory(
                staging,
                destination,
                context,
                deadline=budget.deadline,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _resolve_paths(
    context: ToolContext,
    source_input: str,
    destination_input: str,
    *,
    destination_suffix: str | None = None,
) -> tuple[Path, Path]:
    source = resolve_tool_path(context, source_input, "read")
    requested_destination = resolve_tool_path(context, destination_input, "write")
    destination = requested_destination
    if destination_suffix and not str(requested_destination).endswith(destination_suffix):
        destination = resolve_tool_path(
            context,
            destination_input + destination_suffix,
            "write",
        )
    return source, destination


def _resolve_extraction_paths(
    context: ToolContext,
    source_input: str,
    destination_input: str,
) -> tuple[Path, Path]:
    source = resolve_tool_path(context, source_input, "read")
    if destination_input:
        destination = resolve_tool_path(context, destination_input, "write")
    else:
        default_destination = source.parent / source.stem
        destination = resolve_tool_path(context, str(default_destination), "write")
    return source, destination


def _temporary_output(destination: Path, context: ToolContext) -> Path:
    _mutation_checkpoint(context)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(temp_name)


def _path_error_result(error: BaseException) -> ToolResult:
    return ToolResult(ok=False, output=f"error[permission_denied]: {error}")


def _display(path: Path, context: ToolContext) -> str:
    return relative_display_path(path, context.cwd)


# ---------------------------------------------------------------------------
# Gzip Compress
# ---------------------------------------------------------------------------

def _validate_gzip_compress(input_data: dict) -> dict:
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    return {"source": source.strip(), "destination": destination.strip()}


def _run_gzip_compress(input_data: dict, context: ToolContext) -> ToolResult:
    try:
        source, destination = _resolve_paths(
            context,
            input_data["source"],
            input_data["destination"],
        )
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except (PermissionError, PermissionDeniedError) as error:
        return _path_error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")
    if not source.is_file():
        return ToolResult(ok=False, output="Use tar_archive for directories")

    temporary: Path | None = None
    try:
        temporary = _temporary_output(destination, context)
        with open(source, "rb") as source_file, gzip.open(temporary, "wb") as output:
            _copy_with_checkpoints(source_file, output, context)
        _mutation_checkpoint(context)
        temporary.replace(destination)
        return ToolResult(ok=True, output=f"Compressed to {_display(destination, context)}")
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except Exception as error:
        return ToolResult(ok=False, output=f"Compression error: {error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


gzip_compress_tool = ToolDefinition(
    name="gzip_compress",
    description="Compress a file using gzip.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source file (relative to workspace)"},
            "destination": {"type": "string", "description": "Output .gz file path"}
        },
        "required": ["source", "destination"]
    },
    validator=_validate_gzip_compress,
    run=_run_gzip_compress,
)


# ---------------------------------------------------------------------------
# Gzip Decompress
# ---------------------------------------------------------------------------

def _validate_gzip_decompress(input_data: dict) -> dict:
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    return {"source": source.strip(), "destination": destination.strip()}


def _run_gzip_decompress(input_data: dict, context: ToolContext) -> ToolResult:
    try:
        source, destination = _resolve_paths(
            context,
            input_data["source"],
            input_data["destination"],
        )
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except (PermissionError, PermissionDeniedError) as error:
        return _path_error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    budget = _ExtractionBudget(context, _extraction_deadline(context))
    temporary: Path | None = None
    try:
        budget.register_member(0)
        temporary = _temporary_output(destination, context)
        with gzip.open(source, "rb") as source_file, open(temporary, "wb") as output:
            _copy_bounded(source_file, output, budget)
        budget.checkpoint()
        temporary.replace(destination)
        return ToolResult(ok=True, output=f"Decompressed to {_display(destination, context)}")
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except Exception as error:
        return ToolResult(ok=False, output=f"Decompression error: {error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


gzip_decompress_tool = ToolDefinition(
    name="gzip_decompress",
    description="Decompress a .gz file.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source .gz file"},
            "destination": {"type": "string", "description": "Output file path"}
        },
        "required": ["source", "destination"]
    },
    validator=_validate_gzip_decompress,
    run=_run_gzip_decompress,
)


# ---------------------------------------------------------------------------
# Tar Archive
# ---------------------------------------------------------------------------

def _validate_tar_create(input_data: dict) -> dict:
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    mode = input_data.get("mode", "gz")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    if mode not in {"gz", "bz2", "xz", "none"}:
        raise ValueError("mode must be one of: gz, bz2, xz, none")
    return {"source": source.strip(), "destination": destination.strip(), "mode": mode}


def _run_tar_create(input_data: dict, context: ToolContext) -> ToolResult:
    mode = input_data.get("mode", "gz")
    modes = {
        "gz": ("w:gz", ".tar.gz"),
        "bz2": ("w:bz2", ".tar.bz2"),
        "xz": ("w:xz", ".tar.xz"),
        "none": ("w", ".tar"),
    }
    tar_mode, extension = modes[mode]
    try:
        source, destination = _resolve_paths(
            context,
            input_data["source"],
            input_data["destination"],
            destination_suffix=extension,
        )
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except (PermissionError, PermissionDeniedError) as error:
        return _path_error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    temporary: Path | None = None
    try:
        temporary = _temporary_output(destination, context)
        excluded = {temporary.resolve(strict=False), destination.resolve(strict=False)}

        def filter_member(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
            _mutation_checkpoint(context)
            member_source = (source.parent / member.name).resolve(strict=False)
            return None if member_source in excluded else member

        with tarfile.open(temporary, tar_mode) as archive:
            archive.add(source, arcname=source.name, filter=filter_member)
        _mutation_checkpoint(context)
        temporary.replace(destination)
        return ToolResult(ok=True, output=f"Created {_display(destination, context)}")
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except Exception as error:
        return ToolResult(ok=False, output=f"Archive error: {error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


tar_create_tool = ToolDefinition(
    name="tar_create",
    description="Create tar archive (optionally compressed with gz, bz2, or xz).",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "File or directory to archive"},
            "destination": {"type": "string", "description": "Output archive path"},
            "mode": {"type": "string", "description": "Compression: gz, bz2, xz, or none"}
        },
        "required": ["source", "destination"]
    },
    validator=_validate_tar_create,
    run=_run_tar_create,
)


# ---------------------------------------------------------------------------
# Tar Extract
# ---------------------------------------------------------------------------

def _validate_tar_extract(input_data: dict) -> dict:
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if destination is not None and not isinstance(destination, str):
        raise ValueError("destination must be a string")
    return {"source": source.strip(), "destination": destination.strip() if destination else ""}


def _run_tar_extract(input_data: dict, context: ToolContext) -> ToolResult:
    try:
        source, destination = _resolve_extraction_paths(
            context,
            input_data["source"],
            input_data.get("destination", ""),
        )
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except (PermissionError, PermissionDeniedError) as error:
        return _path_error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    try:
        _safe_extract_tar(source, destination, context)
        return ToolResult(ok=True, output=f"Extracted to {_display(destination, context)}")
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except Exception as error:
        return ToolResult(ok=False, output=f"Extract error: {error}")


tar_extract_tool = ToolDefinition(
    name="tar_extract",
    description="Extract tar archive.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Archive file to extract"},
            "destination": {"type": "string", "description": "Output directory (optional)"}
        },
        "required": ["source"]
    },
    validator=_validate_tar_extract,
    run=_run_tar_extract,
)


# ---------------------------------------------------------------------------
# Zip
# ---------------------------------------------------------------------------

def _validate_zip_create(input_data: dict) -> dict:
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination is required")
    return {"source": source.strip(), "destination": destination.strip()}


def _run_zip_create(input_data: dict, context: ToolContext) -> ToolResult:
    try:
        source, destination = _resolve_paths(
            context,
            input_data["source"],
            input_data["destination"],
            destination_suffix=".zip",
        )
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except (PermissionError, PermissionDeniedError) as error:
        return _path_error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    temporary: Path | None = None
    try:
        temporary = _temporary_output(destination, context)
        excluded = {temporary.resolve(strict=False), destination.resolve(strict=False)}
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            if source.is_file():
                _mutation_checkpoint(context)
                archive.write(source, source.name)
            else:
                for item in source.rglob("*"):
                    _mutation_checkpoint(context)
                    resolved_item = resolve_tool_path(context, str(item), "read")
                    if resolved_item in excluded:
                        continue
                    if item.is_file():
                        archive.write(resolved_item, item.relative_to(source.parent))
        _mutation_checkpoint(context)
        temporary.replace(destination)
        return ToolResult(ok=True, output=f"Created {_display(destination, context)}")
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except Exception as error:
        return ToolResult(ok=False, output=f"Zip error: {error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


zip_create_tool = ToolDefinition(
    name="zip_create",
    description="Create ZIP archive.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "File or directory to archive"},
            "destination": {"type": "string", "description": "Output .zip path"}
        },
        "required": ["source", "destination"]
    },
    validator=_validate_zip_create,
    run=_run_zip_create,
)


# ---------------------------------------------------------------------------
# Zip Extract
# ---------------------------------------------------------------------------

def _validate_zip_extract(input_data: dict) -> dict:
    source = input_data.get("source", "")
    destination = input_data.get("destination", "")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    if destination is not None and not isinstance(destination, str):
        raise ValueError("destination must be a string")
    return {"source": source.strip(), "destination": destination.strip() if destination else ""}


def _run_zip_extract(input_data: dict, context: ToolContext) -> ToolResult:
    try:
        source, destination = _resolve_extraction_paths(
            context,
            input_data["source"],
            input_data.get("destination", ""),
        )
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except (PermissionError, PermissionDeniedError) as error:
        return _path_error_result(error)

    if not source.exists():
        return ToolResult(ok=False, output=f"Source not found: {input_data['source']}")

    try:
        _safe_extract_zip(source, destination, context)
        return ToolResult(ok=True, output=f"Extracted to {_display(destination, context)}")
    except (TurnCancellationRequested, TaskDeadlineExceeded):
        raise
    except Exception as error:
        return ToolResult(ok=False, output=f"Extract error: {error}")


zip_extract_tool = ToolDefinition(
    name="zip_extract",
    description="Extract ZIP archive.",
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Archive file to extract"},
            "destination": {"type": "string", "description": "Output directory (optional)"}
        },
        "required": ["source"]
    },
    validator=_validate_zip_extract,
    run=_run_zip_extract,
)
