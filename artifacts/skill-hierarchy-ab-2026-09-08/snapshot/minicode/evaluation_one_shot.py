"""Cooperative local one-shot claim for evaluators that must not reuse a holdout.

An evaluator must call :func:`claim_evaluation_run` after completing read-only
local validation and before its first remote request.  This lets malformed or
misaddressed local inputs fail preflight without consuming the one-shot while
ensuring no scored remote inference can happen before the claim.  Once the
marker has been created, cooperating processes using the same directory reject
every later attempt, even when the first evaluator failed before producing its
output artifact.  A filesystem owner can delete the marker or replace an
ancestor directory, so this is not a cryptographic or adversarial replay
guarantee; externally auditable runs need an append-only CI record or signed
attestation in addition to this guard.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

EVALUATION_ONE_SHOT_SCHEMA_VERSION = 1

_HEX_DIGITS = frozenset("0123456789abcdef")
_HAS_DIRECTORY_FD = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)
_MARKER_FIELDS = frozenset(
    {
        "schema_version",
        "dataset_id",
        "holdout_sha256",
        "output_artifact",
        "marker_path",
        "claimed_at_utc",
    }
)


def validate_evaluation_one_shot_receipt(
    value: object,
    *,
    dataset_id: str,
    holdout_sha256: str,
    output_artifact: str,
    marker_name: str,
) -> dict[str, int | str]:
    """Validate the closed, content-free receipt embedded in an evaluation.

    This verifies identity and schema, not merely that a marker-shaped object
    was supplied.  The evaluator still has to obtain the receipt from
    :func:`claim_evaluation_run` after local preflight and before the first
    scored remote request.
    """

    expected_dataset = _validate_dataset_id(dataset_id)
    expected_sha = _validate_holdout_sha256(holdout_sha256)
    expected_output = _validate_output_artifact(output_artifact)
    if (
        not isinstance(marker_name, str)
        or marker_name in {"", ".", ".."}
        or "/" in marker_name
        or "\\" in marker_name
        or any(
            character.isspace() or not character.isprintable()
            for character in marker_name
        )
    ):
        raise ValueError("marker_name must be a safe basename")
    if not isinstance(value, dict) or set(value) != _MARKER_FIELDS:
        raise ValueError("evaluation one-shot receipt schema mismatch")
    claimed_at = value.get("claimed_at_utc")
    if not isinstance(claimed_at, str) or not claimed_at.endswith("Z"):
        raise ValueError("evaluation one-shot receipt timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(claimed_at.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError("evaluation one-shot receipt timestamp is invalid") from error
    if (
        parsed.tzinfo != timezone.utc
        or parsed.isoformat().replace("+00:00", "Z") != claimed_at
    ):
        raise ValueError("evaluation one-shot receipt timestamp is invalid")
    if (
        value.get("schema_version") != EVALUATION_ONE_SHOT_SCHEMA_VERSION
        or value.get("dataset_id") != expected_dataset
        or value.get("holdout_sha256") != expected_sha
        or value.get("output_artifact") != expected_output
        or value.get("marker_path") != marker_name
    ):
        raise ValueError("evaluation one-shot receipt identity mismatch")
    return {
        "schema_version": EVALUATION_ONE_SHOT_SCHEMA_VERSION,
        "dataset_id": expected_dataset,
        "holdout_sha256": expected_sha,
        "output_artifact": expected_output,
        "marker_path": marker_name,
        "claimed_at_utc": claimed_at,
    }


class EvaluationOneShotError(RuntimeError):
    """Base error for an evaluation one-shot claim."""


class EvaluationAlreadyConsumedError(EvaluationOneShotError):
    """Raised when the marker already exists and the run is spent."""


class EvaluationOneShotPathError(EvaluationOneShotError):
    """Raised when the marker cannot be created at a safe path."""


@dataclass(frozen=True, slots=True)
class EvaluationOneShotReceipt:
    """Immutable public metadata persisted by a successful one-shot claim."""

    schema_version: int
    dataset_id: str
    holdout_sha256: str
    output_artifact: str
    marker_path: str
    claimed_at_utc: str

    def _as_payload(self) -> dict[str, int | str]:
        payload: dict[str, int | str] = {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "holdout_sha256": self.holdout_sha256,
            "output_artifact": self.output_artifact,
            "marker_path": self.marker_path,
            "claimed_at_utc": self.claimed_at_utc,
        }
        if payload.keys() != _MARKER_FIELDS:  # pragma: no cover - local invariant
            raise AssertionError("evaluation one-shot marker schema changed")
        return payload


def _validate_dataset_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(
            character.isspace() or not character.isprintable() for character in value
        )
    ):
        raise ValueError("dataset_id must be a non-empty public identifier")
    return value


def _validate_holdout_sha256(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX_DIGITS for character in value)
    ):
        raise ValueError("holdout_sha256 must be a lowercase SHA-256 hex digest")
    return value


def _validate_output_artifact(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("output_artifact must be a non-empty relative path")
    if "\\" in value or any(not character.isprintable() for character in value):
        raise ValueError("output_artifact must be a safe relative POSIX path")
    artifact = PurePosixPath(value)
    windows_artifact = PureWindowsPath(value)
    if (
        artifact.is_absolute()
        or windows_artifact.drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("output_artifact must be a safe relative POSIX path")
    return artifact.as_posix()


def _validate_marker_path(marker_path: Path) -> tuple[Path, str]:
    if not isinstance(marker_path, Path):
        raise TypeError("marker_path must be a pathlib.Path")
    marker_name = marker_path.name
    if marker_name in {"", ".", ".."} or any(
        character.isspace() or not character.isprintable() for character in marker_name
    ):
        raise EvaluationOneShotPathError("evaluation marker filename is unsafe")
    # Persist only a basename.  It is useful to callers while never disclosing
    # an absolute host path in a portable/public evaluation artifact.
    return marker_path.parent, marker_name


def _open_safe_parent(parent: Path) -> int:
    try:
        metadata = os.lstat(parent)
    except OSError as error:
        raise EvaluationOneShotPathError(
            "evaluation marker parent must already exist"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise EvaluationOneShotPathError(
            "evaluation marker parent must be a non-symlink directory"
        )

    # A full-path fallback would reintroduce a symlink-swap window between the
    # lstat above and O_EXCL creation.  Fail closed on platforms (notably
    # Windows) that cannot anchor the operation to the validated directory.
    if not _HAS_DIRECTORY_FD:
        raise EvaluationOneShotPathError(
            "secure directory descriptors are unavailable on this platform"
        )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as error:
        raise EvaluationOneShotPathError(
            "evaluation marker parent could not be opened safely"
        ) from error
    opened_metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(opened_metadata.st_mode) or (
        opened_metadata.st_dev,
        opened_metadata.st_ino,
    ) != (metadata.st_dev, metadata.st_ino):  # pragma: no cover - defensive race guard
        os.close(descriptor)
        raise EvaluationOneShotPathError(
            "evaluation marker parent changed during validation"
        )
    return descriptor


def _marker_is_symlink(
    parent_descriptor: int,
    marker_name: str,
) -> bool:
    try:
        metadata = os.stat(
            marker_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode)


def _open_exclusive_marker(
    parent_descriptor: int,
    marker_name: str,
) -> int:
    if _marker_is_symlink(parent_descriptor, marker_name):
        raise EvaluationOneShotPathError("evaluation marker must not be a symlink")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(marker_name, flags, 0o600, dir_fd=parent_descriptor)
    except OSError as error:
        if error.errno == errno.EEXIST:
            if _marker_is_symlink(parent_descriptor, marker_name):
                raise EvaluationOneShotPathError(
                    "evaluation marker must not be a symlink"
                ) from error
            raise EvaluationAlreadyConsumedError(
                "evaluation one-shot marker already exists"
            ) from error
        if error.errno == errno.ELOOP:
            raise EvaluationOneShotPathError(
                "evaluation marker must not be a symlink"
            ) from error
        raise EvaluationOneShotPathError(
            "evaluation one-shot marker could not be created"
        ) from error


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:  # pragma: no cover - operating-system contract guard
            raise OSError(
                errno.EIO, "evaluation one-shot marker write made no progress"
            )
        view = view[written:]


def claim_evaluation_run(
    marker_path: Path,
    *,
    dataset_id: str,
    holdout_sha256: str,
    output_artifact: str,
) -> EvaluationOneShotReceipt:
    """Claim one cooperative local evaluator run and return its durable receipt.

    The marker's parent must already exist and must not be a symlink.  This
    function never creates a directory, reads a holdout, touches the output
    artifact, removes a marker, or offers a reset path.
    """

    parent, marker_name = _validate_marker_path(marker_path)
    receipt = EvaluationOneShotReceipt(
        schema_version=EVALUATION_ONE_SHOT_SCHEMA_VERSION,
        dataset_id=_validate_dataset_id(dataset_id),
        holdout_sha256=_validate_holdout_sha256(holdout_sha256),
        output_artifact=_validate_output_artifact(output_artifact),
        marker_path=marker_name,
        claimed_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    encoded = (
        json.dumps(
            receipt._as_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    parent_descriptor = _open_safe_parent(parent)
    try:
        marker_descriptor = _open_exclusive_marker(
            parent_descriptor,
            marker_name,
        )
        try:
            _write_all(marker_descriptor, encoded)
            os.fsync(marker_descriptor)
        finally:
            os.close(marker_descriptor)
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    return receipt


__all__ = [
    "EVALUATION_ONE_SHOT_SCHEMA_VERSION",
    "EvaluationAlreadyConsumedError",
    "EvaluationOneShotError",
    "EvaluationOneShotPathError",
    "EvaluationOneShotReceipt",
    "claim_evaluation_run",
    "validate_evaluation_one_shot_receipt",
]
