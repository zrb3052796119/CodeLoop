from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_persistent_memory_repair_acceptance_v5_manifest import (
    DEFAULT_OUTPUT,
    RUNTIME_PROFILE_IDENTITY,
    SUITE_ID,
    V4_MANIFEST,
    build_manifest,
)
from scripts.run_north_star_live import (
    _runtime_profile_contract,
    _validate_manifest,
)


def _expected_v5_cases() -> list[dict]:
    source = json.loads(V4_MANIFEST.read_text(encoding="utf-8"))
    cases = deepcopy(source["cases"])
    for case in cases:
        for index, oracle in enumerate(case["oracles"]):
            if oracle["id"] == "verification-ran":
                case["oracles"][index] = {
                    "everyTurn": True,
                    "id": "verification-ran",
                    "kind": "verification_passed",
                    "min": 1,
                    "sources": ["run_command_exit", "test_runner"],
                    "verificationKind": "tests",
                }
    return cases


def test_v5_hashes_and_parses_one_v4_manifest_byte_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    frozen_payload = V4_MANIFEST.read_bytes()
    tampered = json.loads(frozen_payload)
    tampered_marker = "tampered-after-identity-check\n"
    tampered["cases"][0]["files"]["README.md"] = tampered_marker
    manifest_byte_reads: list[Path] = []
    manifest_text_reads: list[Path] = []

    def tracked_read_bytes(path: Path) -> bytes:
        if path == V4_MANIFEST:
            manifest_byte_reads.append(path)
            return frozen_payload
        return original_read_bytes(path)

    def changed_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == V4_MANIFEST:
            manifest_text_reads.append(path)
            return json.dumps(tampered)
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(Path, "read_text", changed_read_text)

    manifest = build_manifest()

    assert manifest_byte_reads == [V4_MANIFEST]
    assert manifest_text_reads == []
    assert manifest["cases"][0]["files"]["README.md"] != tampered_marker


def test_v5_changes_only_verification_tool_coupling_from_frozen_v4() -> None:
    manifest = build_manifest()

    assert manifest["suiteId"] == SUITE_ID
    assert manifest["cases"] == _expected_v5_cases()
    assert len(_validate_manifest(manifest)) == 10
    assert manifest["supersedes"]["retainedAsFailureEvidence"] is True
    assert manifest["supersedes"]["firstAttemptPassedCases"] == 7
    profile = _runtime_profile_contract(manifest)
    assert profile is not None
    assert {
        key: profile[key] for key in RUNTIME_PROFILE_IDENTITY
    } == RUNTIME_PROFILE_IDENTITY
    assert len(profile["profileSha256"]) == 64


def test_v5_has_tool_neutral_verification_and_exact_memory_attribution() -> None:
    manifest = build_manifest()
    verification_oracles = [
        oracle
        for case in manifest["cases"]
        for oracle in case["oracles"]
        if oracle["id"] == "verification-ran"
    ]

    assert len(verification_oracles) == 8
    assert all(
        oracle == {
            "everyTurn": True,
            "id": "verification-ran",
            "kind": "verification_passed",
            "min": 1,
            "sources": ["run_command_exit", "test_runner"],
            "verificationKind": "tests",
        }
        for oracle in verification_oracles
    )
    for case in manifest["cases"]:
        attribution = [
            oracle
            for oracle in case["oracles"]
            if oracle["kind"] == "memory_attributed"
        ]
        assert len(attribution) == 1
        assert attribution[0]["id"] == "lesson-attributed"


def test_frozen_v5_artifact_is_byte_identical_to_the_builder() -> None:
    expected = (
        json.dumps(
            build_manifest(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    assert DEFAULT_OUTPUT.read_bytes() == expected
    assert hashlib.sha256(expected).hexdigest() == (
        "72db4d34a756fe63d35e978e4a65a38bf95f16d1de88fc17da9f59efd8cfc288"
    )
