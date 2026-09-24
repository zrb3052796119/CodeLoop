from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from threading import Barrier

import pytest

import minicode.evaluation_one_shot as one_shot
from minicode.evaluation_one_shot import (
    EVALUATION_ONE_SHOT_SCHEMA_VERSION,
    EvaluationAlreadyConsumedError,
    EvaluationOneShotPathError,
    claim_evaluation_run,
    validate_evaluation_one_shot_receipt,
)

_DATASET_ID = "public-holdout-v1"
_HOLDOUT_SHA256 = "a" * 64
_OUTPUT_ARTIFACT = "artifacts/public-evaluation.json"
_EXPECTED_FIELDS = {
    "schema_version",
    "dataset_id",
    "holdout_sha256",
    "output_artifact",
    "marker_path",
    "claimed_at_utc",
}
_requires_safe_directory_fd = pytest.mark.skipif(
    not one_shot._HAS_DIRECTORY_FD,
    reason="one-shot claims fail closed without safe directory descriptors",
)


def _claim(marker: Path):
    return claim_evaluation_run(
        marker,
        dataset_id=_DATASET_ID,
        holdout_sha256=_HOLDOUT_SHA256,
        output_artifact=_OUTPUT_ARTIFACT,
    )


def _valid_receipt() -> dict[str, int | str]:
    return {
        "schema_version": EVALUATION_ONE_SHOT_SCHEMA_VERSION,
        "dataset_id": _DATASET_ID,
        "holdout_sha256": _HOLDOUT_SHA256,
        "output_artifact": _OUTPUT_ARTIFACT,
        "marker_path": "evaluation.consumed.json",
        "claimed_at_utc": "2026-09-03T00:00:00Z",
    }


def test_embedded_receipt_validator_binds_every_identity_field() -> None:
    receipt = _valid_receipt()

    assert (
        validate_evaluation_one_shot_receipt(
            receipt,
            dataset_id=_DATASET_ID,
            holdout_sha256=_HOLDOUT_SHA256,
            output_artifact=_OUTPUT_ARTIFACT,
            marker_name="evaluation.consumed.json",
        )
        == receipt
    )

    for field, replacement in (
        ("dataset_id", "different-holdout"),
        ("holdout_sha256", "b" * 64),
        ("output_artifact", "artifacts/replay.json"),
        ("marker_path", "alternate.consumed.json"),
    ):
        tampered = {**receipt, field: replacement}
        with pytest.raises(ValueError, match="identity mismatch"):
            validate_evaluation_one_shot_receipt(
                tampered,
                dataset_id=_DATASET_ID,
                holdout_sha256=_HOLDOUT_SHA256,
                output_artifact=_OUTPUT_ARTIFACT,
                marker_name="evaluation.consumed.json",
            )


def test_embedded_receipt_validator_rejects_schema_and_timestamp_ambiguity() -> None:
    with pytest.raises(ValueError, match="schema mismatch"):
        validate_evaluation_one_shot_receipt(
            {**_valid_receipt(), "extra": True},
            dataset_id=_DATASET_ID,
            holdout_sha256=_HOLDOUT_SHA256,
            output_artifact=_OUTPUT_ARTIFACT,
            marker_name="evaluation.consumed.json",
        )
    with pytest.raises(ValueError, match="timestamp is invalid"):
        validate_evaluation_one_shot_receipt(
            {**_valid_receipt(), "claimed_at_utc": "2026-09-03T08:00:00+08:00"},
            dataset_id=_DATASET_ID,
            holdout_sha256=_HOLDOUT_SHA256,
            output_artifact=_OUTPUT_ARTIFACT,
            marker_name="evaluation.consumed.json",
        )


@_requires_safe_directory_fd
def test_claim_persists_closed_public_schema_and_returns_frozen_receipt(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "evaluation.consumed.json"

    receipt = _claim(marker)

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert set(payload) == _EXPECTED_FIELDS
    assert payload == {
        "schema_version": EVALUATION_ONE_SHOT_SCHEMA_VERSION,
        "dataset_id": _DATASET_ID,
        "holdout_sha256": _HOLDOUT_SHA256,
        "output_artifact": _OUTPUT_ARTIFACT,
        "marker_path": marker.name,
        "claimed_at_utc": receipt.claimed_at_utc,
    }
    assert receipt.schema_version == EVALUATION_ONE_SHOT_SCHEMA_VERSION
    assert receipt.dataset_id == _DATASET_ID
    assert receipt.holdout_sha256 == _HOLDOUT_SHA256
    assert receipt.output_artifact == _OUTPUT_ARTIFACT
    assert receipt.marker_path == marker.name
    assert (
        datetime.fromisoformat(receipt.claimed_at_utc).utcoffset().total_seconds() == 0
    )
    assert str(tmp_path) not in marker.read_text(encoding="utf-8")
    with pytest.raises(FrozenInstanceError):
        receipt.dataset_id = "changed"  # type: ignore[misc]
    if os.name == "posix":
        assert stat.S_IMODE(marker.stat().st_mode) == 0o600


@_requires_safe_directory_fd
def test_claim_fsyncs_marker_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced_modes: list[int] = []
    real_fsync = one_shot.os.fsync

    def recording_fsync(descriptor: int) -> None:
        synced_modes.append(os.fstat(descriptor).st_mode)
        real_fsync(descriptor)

    monkeypatch.setattr(one_shot.os, "fsync", recording_fsync)

    _claim(tmp_path / "evaluation.consumed.json")

    assert len(synced_modes) == 2
    assert stat.S_ISREG(synced_modes[0])
    assert stat.S_ISDIR(synced_modes[1])


def test_claim_fails_closed_when_directory_descriptors_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "evaluation.consumed.json"
    monkeypatch.setattr(one_shot, "_HAS_DIRECTORY_FD", False)

    with pytest.raises(EvaluationOneShotPathError, match="unavailable"):
        _claim(marker)

    assert not marker.exists()


@_requires_safe_directory_fd
def test_existing_marker_is_always_rejected_without_inspecting_or_replacing_it(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "evaluation.consumed.json"
    original = b"failed-before-json-was-complete"
    marker.write_bytes(original)

    with pytest.raises(EvaluationAlreadyConsumedError, match="already exists"):
        _claim(marker)

    assert marker.read_bytes() == original


@_requires_safe_directory_fd
def test_failure_after_exclusive_create_permanently_spends_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "evaluation.consumed.json"

    def fail_after_create(descriptor: int, payload: bytes) -> None:
        raise OSError("injected marker write failure")

    monkeypatch.setattr(one_shot, "_write_all", fail_after_create)
    with pytest.raises(OSError, match="injected marker write failure"):
        _claim(marker)

    assert marker.exists()
    assert marker.read_bytes() == b""
    monkeypatch.undo()
    with pytest.raises(EvaluationAlreadyConsumedError, match="already exists"):
        _claim(marker)


@_requires_safe_directory_fd
def test_concurrent_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    marker = tmp_path / "evaluation.consumed.json"
    contenders = 24
    barrier = Barrier(contenders)

    def attempt_claim() -> bool:
        barrier.wait()
        try:
            _claim(marker)
        except EvaluationAlreadyConsumedError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=contenders) as executor:
        outcomes = list(executor.map(lambda _: attempt_claim(), range(contenders)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == contenders - 1
    assert set(json.loads(marker.read_text(encoding="utf-8"))) == _EXPECTED_FIELDS


def test_missing_parent_is_rejected_and_never_created(tmp_path: Path) -> None:
    parent = tmp_path / "missing"

    with pytest.raises(EvaluationOneShotPathError, match="must already exist"):
        _claim(parent / "evaluation.consumed.json")

    assert not parent.exists()


def test_non_directory_parent_is_rejected(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("public fixture", encoding="utf-8")

    with pytest.raises(EvaluationOneShotPathError, match="non-symlink directory"):
        _claim(parent / "evaluation.consumed.json")


def test_symlink_parent_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "real-parent"
    target.mkdir()
    parent = tmp_path / "linked-parent"
    try:
        parent.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(EvaluationOneShotPathError, match="non-symlink directory"):
        _claim(parent / "evaluation.consumed.json")

    assert list(target.iterdir()) == []


@_requires_safe_directory_fd
def test_symlink_marker_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "public-target.json"
    target.write_text("unchanged", encoding="utf-8")
    marker = tmp_path / "evaluation.consumed.json"
    try:
        marker.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(EvaluationOneShotPathError, match="must not be a symlink"):
        _claim(marker)

    assert target.read_text(encoding="utf-8") == "unchanged"
    assert marker.is_symlink()


@pytest.mark.parametrize(
    ("overrides", "error_type", "message"),
    [
        ({"dataset_id": ""}, ValueError, "dataset_id"),
        ({"dataset_id": "not public"}, ValueError, "dataset_id"),
        ({"holdout_sha256": "A" * 64}, ValueError, "holdout_sha256"),
        ({"holdout_sha256": "a" * 63}, ValueError, "holdout_sha256"),
        ({"output_artifact": "/tmp/result.json"}, ValueError, "relative"),
        ({"output_artifact": "C:/result.json"}, ValueError, "relative"),
        ({"output_artifact": "../result.json"}, ValueError, "relative"),
        ({"output_artifact": "artifacts\\result.json"}, ValueError, "relative"),
    ],
)
def test_invalid_public_metadata_is_rejected_before_marker_creation(
    tmp_path: Path,
    overrides: dict[str, str],
    error_type: type[Exception],
    message: str,
) -> None:
    marker = tmp_path / "evaluation.consumed.json"
    arguments = {
        "dataset_id": _DATASET_ID,
        "holdout_sha256": _HOLDOUT_SHA256,
        "output_artifact": _OUTPUT_ARTIFACT,
        **overrides,
    }

    with pytest.raises(error_type, match=message):
        claim_evaluation_run(marker, **arguments)

    assert not marker.exists()


def test_marker_requires_path_object_and_safe_filename(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        claim_evaluation_run(  # type: ignore[arg-type]
            str(tmp_path / "marker.json"),
            dataset_id=_DATASET_ID,
            holdout_sha256=_HOLDOUT_SHA256,
            output_artifact=_OUTPUT_ARTIFACT,
        )

    unsafe_marker = tmp_path / "marker\nname.json"
    with pytest.raises(EvaluationOneShotPathError, match="filename is unsafe"):
        _claim(unsafe_marker)
    assert not unsafe_marker.exists()
